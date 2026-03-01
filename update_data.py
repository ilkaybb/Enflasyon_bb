import os
import pandas as pd
from bs4 import BeautifulSoup
import re
from datetime import datetime
from github import Github
from github.GithubException import GithubException
from io import BytesIO
import zipfile
import base64

# --- AYARLAR ---
EXCEL_DOSYASI = "TUFE_Konfigurasyon.xlsx"
FIYAT_DOSYASI = "Fiyat_Veritabani.xlsx"
SAYFA_ADI = "Madde_Sepeti"
BRANCH_NAME = "main" # Veya "master", repo branch adınız neyse

# --- GITHUB BAĞLANTISI ---
def get_github_repo():
    token = os.environ.get("GH_TOKEN")
    repo_name = os.environ.get("REPO_NAME")
    if not token or not repo_name:
        print("HATA: Token veya Repo adı bulunamadı.")
        return None
    g = Github(token)
    return g.get_repo(repo_name)
    
def github_file_to_bytes(content_file, repo=None):
    try:
        return content_file.decoded_content
    except Exception:
        if repo and getattr(content_file, "sha", None):
            blob = repo.get_git_blob(content_file.sha)
            return base64.b64decode(blob.content)
        raise

def github_excel_oku(dosya_adi, sayfa_adi=None):
    repo = get_github_repo()
    if not repo: return pd.DataFrame()
    try:
        c = repo.get_contents(dosya_adi, ref=BRANCH_NAME)
        if sayfa_adi:
            df = pd.read_excel(BytesIO(github_file_to_bytes(c, repo)), sheet_name=sayfa_adi, dtype=str)
        else:
            df = pd.read_excel(BytesIO(github_file_to_bytes(c, repo)), dtype=str)
        return df
    except Exception as e:
        print(f"Excel Okuma Hatası ({dosya_adi}): {e}")
        return pd.DataFrame()

def github_excel_guncelle(df_yeni, dosya_adi):
    repo = get_github_repo()
    if not repo: return "Repo Yok"
    try:
        c = None        
        try:
            c = repo.get_contents(dosya_adi, ref=BRANCH_NAME)
            old = pd.read_excel(BytesIO(github_file_to_bytes(c, repo)), dtype=str)
            yeni_tarih = str(df_yeni['Tarih'].iloc[0])
            # Aynı tarih ve kod varsa eskisini çıkar
            old = old[~((old['Tarih'].astype(str) == yeni_tarih) & (old['Kod'].isin(df_yeni['Kod'])))]
            final = pd.concat([old, df_yeni], ignore_index=True)
        except GithubException as e:
            if e.status == 404:
                final = df_yeni
            else:
                raise
        
        out = BytesIO()
        with pd.ExcelWriter(out, engine='openpyxl') as w:
            final.to_excel(w, index=False, sheet_name='Fiyat_Log')
        
        msg = f"Otomatik Veri Güncellemesi: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        if c:
            repo.update_file(c.path, msg, out.getvalue(), c.sha, branch=BRANCH_NAME)
        else:
            repo.create_file(dosya_adi, msg, out.getvalue(), branch=BRANCH_NAME)
        return "OK"
    except Exception as e:
        return str(e)

# --- YARDIMCI FONKSİYONLAR ---
def temizle_fiyat(t):
    if not t: return None
    t = str(t).replace('TL', '').replace('₺', '').strip()
    t = t.replace('.', '').replace(',', '.') if ',' in t and '.' in t else t.replace(',', '.')
    try:
        return float(re.sub(r'[^\d.]', '', t))
    except:
        return None

def kod_standartlastir(k): return str(k).replace('.0', '').strip().zfill(7)

def fiyat_bul_siteye_gore(soup, url):
    fiyat = 0
    kaynak = ""
    domain = url.lower() if url else ""

    if "cimri" in domain:
        try:
            cimri_tag = soup.find("span", class_="yEvpr")
            if cimri_tag:
                raw_txt = cimri_tag.get_text()
                if v := temizle_fiyat(raw_txt):
                    return v, "Cimri-Bot"
        except: pass

    if m := re.search(r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*(?:TL|₺)', soup.get_text()[:5000]):
        if v := temizle_fiyat(m.group(1)): 
            fiyat = v
            kaynak = "Regex"
            
    return fiyat, kaynak

# --- ANA İŞLEYİCİ ---
def run_update():
    print("Güncelleme başlatılıyor...")
    repo = get_github_repo()
    if not repo: return
    
    # 1. Konfigürasyon Oku
    df_conf = github_excel_oku(EXCEL_DOSYASI, SAYFA_ADI)
    if df_conf.empty:
        print("Konfigürasyon dosyası okunamadı.")
        return

    df_conf.columns = df_conf.columns.str.strip()
    kod_col = next((c for c in df_conf.columns if c.lower() == 'kod'), None)
    url_col = next((c for c in df_conf.columns if c.lower() == 'url'), None)
    ad_col = next((c for c in df_conf.columns if 'ad' in c.lower()), 'Madde adı')
    manuel_col = next((c for c in df_conf.columns if 'manuel' in c.lower() and 'fiyat' in c.lower()), None)

    if not kod_col or not url_col: 
        print("Sütunlar eksik.")
        return
    
    df_conf['Kod'] = df_conf[kod_col].astype(str).apply(kod_standartlastir)
    url_map = {str(row[url_col]).strip(): row for _, row in df_conf.iterrows() if pd.notna(row[url_col])}
    
    veriler = []
    bugun = datetime.now().strftime("%Y-%m-%d")
    simdi = datetime.now().strftime("%H:%M")

    # A. Manuel Fiyatlar
    if manuel_col:
        for _, row in df_conf.iterrows():
            try:
                raw_manuel = row[manuel_col]
                fiyat_manuel = temizle_fiyat(raw_manuel)
                if fiyat_manuel and fiyat_manuel > 0:
                    veriler.append({
                        "Tarih": bugun, "Zaman": simdi,
                        "Kod": kod_standartlastir(row[kod_col]),
                        "Madde_Adi": row[ad_col],
                        "Fiyat": float(fiyat_manuel),
                        "Kaynak": "Manuel_Fiyat", "URL": "MANUEL"
                    })
            except: continue

    # B. HTML Taraması
    print("HTML dosyaları taranıyor...")
    contents = repo.get_contents("", ref=BRANCH_NAME)
    zip_files = [c for c in contents if c.name.endswith(".zip") and c.name.startswith("Bolum")]
    
    for zip_file in zip_files:
        try:
            blob = repo.get_git_blob(zip_file.sha)
            zip_data = base64.b64decode(blob.content)
            with zipfile.ZipFile(BytesIO(zip_data)) as z:
                for file_name in z.namelist():
                    if not file_name.endswith(('.html', '.htm')): continue
                    with z.open(file_name) as f:
                        raw = f.read().decode("utf-8", errors="ignore")
                        soup = BeautifulSoup(raw, 'html.parser')
                        found_url = None
                        if c := soup.find("link", rel="canonical"): found_url = c.get("href")
                        
                        if found_url and str(found_url).strip() in url_map:
                            target = url_map[str(found_url).strip()]
                            fiyat, kaynak = fiyat_bul_siteye_gore(soup, target[url_col])
                            if fiyat > 0:
                                veriler.append({
                                    "Tarih": bugun, "Zaman": simdi,
                                    "Kod": target['Kod'],
                                    "Madde_Adi": target[ad_col],
                                    "Fiyat": float(fiyat),
                                    "Kaynak": kaynak, "URL": target[url_col]
                                })
        except Exception as e:
            print(f"Zip Hatası ({zip_file.name}): {e}")

    if veriler:
        sonuc = github_excel_guncelle(pd.DataFrame(veriler), FIYAT_DOSYASI)
        print(f"İşlem Sonucu: {sonuc}")
    else:
        print("Eklenecek yeni veri bulunamadı.")

if __name__ == "__main__":
    run_update()
