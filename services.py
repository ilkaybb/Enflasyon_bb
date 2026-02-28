import pandas as pd
import numpy as np
from github import Github
from io import BytesIO
from datetime import datetime
import re
# Prophet kütüphanesini artık kullanmasak da import hatası vermesin diye tutabiliriz veya silebiliriz.
# Şimdilik temizlik açısından tutuyoruz ama kullanmıyoruz.
from prophet import Prophet
from fpdf import FPDF
import tempfile
import urllib3
import logging
import math  # Matematik işlemleri için
from config import settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger('cmdstanpy').setLevel(logging.WARNING)
logging.getLogger('prophet').setLevel(logging.WARNING)
pd.set_option('future.no_silent_downcasting', True)

GLOBAL_CACHE = {"data": None, "last_update": None, "is_calculating": False}


class PDFReport(FPDF):
    def __init__(self):
        super().__init__()
        self.font_family = 'Arial'
        self.c_sari = (251, 191, 36)
        self.c_lacivert = (15, 23, 42)

    def fix_text(self, text):
        if text is None: return ""
        text = str(text)
        tr_map = {
            'ğ': 'g', 'Ğ': 'G', 'ü': 'u', 'Ü': 'U', 'ş': 's', 'Ş': 'S',
            'ı': 'i', 'İ': 'I', 'ö': 'o', 'Ö': 'O', 'ç': 'c', 'Ç': 'C',
            'TL': 'TRY', '₺': 'TRY'
        }
        for tr, eng in tr_map.items(): text = text.replace(tr, eng)
        return text.encode('latin-1', 'replace').decode('latin-1')

    def header(self):
        if self.page_no() > 1:
            self.set_font('Arial', 'B', 10)
            self.cell(0, 10, self.fix_text("ENFLASYON MONITORU"), 0, 0, 'L')
            self.ln(10)

    def chapter_title(self, label):
        self.ln(5)
        self.set_font('Arial', 'B', 14)
        self.cell(0, 10, self.fix_text(str(label)), 0, 1, 'L')
        self.ln(10)

    def write_markdown(self, text):
        if not text: return
        self.set_font('Arial', '', 11)
        for line in str(text).split('\n'):
            self.multi_cell(0, 6, self.fix_text(line))
            self.ln(2)

    def create_cover(self, date_str, rate_val):
        self.add_page()
        self.set_fill_color(*self.c_lacivert)
        self.rect(0, 0, 210, 297, 'F')
        self.set_fill_color(255, 255, 255)
        self.rect(20, 40, 170, 200, 'F')
        self.set_y(60)
        self.set_font('Arial', 'B', 28)
        self.set_text_color(15, 23, 42)
        self.cell(0, 15, self.fix_text("PIYASA & ENFLASYON"), 0, 1, 'C')
        self.cell(0, 15, self.fix_text("GORUNUM RAPORU"), 0, 1, 'C')
        self.ln(25)
        self.set_font('Arial', 'B', 70)
        self.set_text_color(239, 68, 68)
        self.cell(0, 30, self.fix_text(f"%{rate_val}"), 0, 1, 'C')
        self.ln(30)
        self.set_font('Arial', '', 12)
        self.set_text_color(100, 100, 100)
        self.multi_cell(0, 6, self.fix_text(f"Rapor Tarihi: {date_str}"), 0, 'C')
        self.set_text_color(0, 0, 0)

    def create_kpi_summary(self, enf, gida, urun):
        self.ln(10)
        self.set_font('Arial', 'B', 12)
        self.cell(0, 10, self.fix_text(f"Manset Enflasyon: %{enf:.2f}"), 0, 1)
        self.cell(0, 10, self.fix_text(f"Gida Enflasyonu: %{gida:.2f}"), 0, 1)
        self.cell(0, 10, self.fix_text(f"En Yuksek Artis: {urun}"), 0, 1)
        self.ln(10)


def standardize_code(k): return str(k).replace('.0', '').strip().zfill(7)


def get_github_repo():
    try:
        return Github(settings.GITHUB_TOKEN, verify=False, timeout=60).get_repo(settings.REPO_NAME)
    except:
        return None


def github_excel_read(file_name, sheet_name=None):
    repo = get_github_repo()
    if not repo: return pd.DataFrame()
    try:
        c = repo.get_contents(file_name, ref=settings.BRANCH)
        if sheet_name: return pd.read_excel(BytesIO(c.decoded_content), sheet_name=sheet_name, dtype=str)
        return pd.read_excel(BytesIO(c.decoded_content), dtype=str)
    except:
        return pd.DataFrame()


def get_official_inflation():
    return pd.DataFrame({'Tarih': [pd.Timestamp("2025-12-30")], 'Resmi_TUFE': [0.89]})


def generate_detailed_static_report(df_analiz, tarih, enf_genel, enf_gida, gun_farki, tahmin, ad_col, agirlik_col):
    inc = df_analiz.sort_values('Fark', ascending=False).head(3)

    def get_row_text(row):
        return f"{row[ad_col]} (%{row['Fark'] * 100:.2f})"

    en_cok_artan_text = ", ".join([get_row_text(row) for _, row in inc.iterrows()])

    sektor_text = "Sektorel veri yok."
    if 'Grup' in df_analiz.columns:
        grup_analiz = df_analiz.groupby('Grup')[[agirlik_col, 'Fark']].apply(
            lambda x: (x['Fark'] * x[agirlik_col]).sum() / x[agirlik_col].sum() * 100
        ).sort_values(ascending=False)
        if not grup_analiz.empty:
            lider_sektor = grup_analiz.index[0]
            lider_oran = grup_analiz.iloc[0]
            sektor_text = f"Sektorel bazda lider: {lider_sektor} (%{lider_oran:.2f})."

    return f"""
PIYASA VE ENFLASYON RAPORU

1. GENEL DURUM
{tarih} itibariyla genel endeks %{enf_genel:.2f}, gida endeksi %{enf_gida:.2f} seviyesindedir.

2. DETAYLAR
{sektor_text}
Zirve yapan urunler: {en_cok_artan_text}.

3. TAHMIN
Yil sonu beklentisi: %{tahmin:.2f}.
""".strip()


def _internal_calculate_metrics():
    print("--- [GITHUB] Veri İndiriliyor... ---")
    df_f = github_excel_read(settings.PRICE_FILE)
    df_s = github_excel_read(settings.EXCEL_FILE, settings.SHEET_NAME)
    if df_f.empty or df_s.empty: return {"error": "Veri Yok"}

    df_s.columns = df_s.columns.str.strip()
    kod_col = next((c for c in df_s.columns if c.lower() == 'kod'), 'Kod')
    ad_col = next((c for c in df_s.columns if 'ad' in c.lower()), 'Madde adı')
    agirlik_col = next((c for c in df_s.columns if 'agirlik' in c.lower().replace('ğ', 'g').replace('ı', 'i')),
                       'Agirlik_2025')

    df_f['Kod'] = df_f['Kod'].astype(str).apply(standardize_code)
    df_s['Kod'] = df_s[kod_col].astype(str).apply(standardize_code)
    df_f['Tarih_DT'] = pd.to_datetime(df_f['Tarih'], errors='coerce')
    df_f = df_f.dropna(subset=['Tarih_DT']).sort_values('Tarih_DT')
    df_f['Tarih_Str'] = df_f['Tarih_DT'].dt.strftime('%Y-%m-%d')
    df_f['Fiyat'] = pd.to_numeric(df_f['Fiyat'], errors='coerce')
    df_f = df_f[df_f['Fiyat'] > 0]

    pivot = df_f.pivot_table(index='Kod', columns='Tarih_Str', values='Fiyat', aggfunc='last').ffill(axis=1).bfill(
        axis=1).reset_index()
    if pivot.empty: return {"error": "Fiyat verisi islenemedi"}

    if 'Grup' not in df_s.columns:
        grup_map = {"01": "Gıda", "02": "Alkol/Tütün", "03": "Giyim", "04": "Konut", "05": "Ev Eşyası", "06": "Sağlık",
                    "07": "Ulaşım", "08": "Haberleşme", "09": "Eğlence", "10": "Eğitim", "11": "Lokanta",
                    "12": "Çeşitli"}
        df_s['Grup'] = df_s['Kod'].str[:2].map(grup_map).fillna("Diğer")

    df_analiz = pd.merge(df_s, pivot, on='Kod', how='left')
    df_analiz[agirlik_col] = pd.to_numeric(df_analiz[agirlik_col], errors='coerce').fillna(1)

    gunler = sorted([c for c in pivot.columns if c != 'Kod'])
    son = gunler[-1]

    # BAZ TARİH - Her zaman önceki ayın son günü baz alınır
    dt_son = datetime.strptime(son, '%Y-%m-%d')

    # Önceki ayın son verili gününü bul
    onceki_ay = f"{dt_son.year}-{dt_son.month-1:02d}"
    onceki_ay_gunleri = [d for d in gunler if d.startswith(onceki_ay)]
    baz_tarih = max(onceki_ay_gunleri) if onceki_ay_gunleri else gunler[0]

    def calculate_geo_mean_series(df, cols):
        data = df[cols].values.astype(float)
        data[data <= 0] = np.nan
        with np.errstate(invalid='ignore'): log_data = np.log(data)
        return np.exp(np.nanmean(log_data, axis=1))

    dt_son = datetime.strptime(son, '%Y-%m-%d')
    bu_ay_str = f"{dt_son.year}-{dt_son.month:02d}"
    bu_ay_cols = [c for c in gunler if c.startswith(bu_ay_str)]
    if not bu_ay_cols: bu_ay_cols = [son]

    df_analiz['Aylik_Ortalama'] = calculate_geo_mean_series(df_analiz, bu_ay_cols)
    df_analiz['Fark'] = (df_analiz['Aylik_Ortalama'] / df_analiz[baz_tarih]) - 1

    valid = df_analiz.dropna(subset=['Fark', agirlik_col])
    enf_genel = 0;
    enf_gida = 0;
    rising_count = 0;
    falling_count = 0;
    waterfall_data = []

    if not valid.empty:
        w = valid[agirlik_col]
        enf_genel = ((w * valid['Fark']).sum() / w.sum()) * 100
        gida = valid[valid['Kod'].str.startswith('01')]
        if not gida.empty:
            wg = gida[agirlik_col]
            enf_gida = ((wg * gida['Fark']).sum() / wg.sum()) * 100
        rising_count = len(valid[valid['Fark'] > 0])
        falling_count = len(valid[valid['Fark'] < 0])
        toplam_agirlik = w.sum()
        valid['Katki'] = (valid['Fark'] * valid[agirlik_col] / toplam_agirlik) * 100
        sektor_katki = valid.groupby('Grup')['Katki'].sum().sort_values(ascending=False)
        waterfall_data = [{"Grup": k, "Katki": v} for k, v in sektor_katki.items()]

    # --- YENİ TAHMİN MANTIĞI: ENFLASYONUN TAM SAYISINI AL ---
    # Örn: 4.03 -> 4.00, 4.99 -> 4.00
    month_end_forecast = math.floor(enf_genel)

    prev_day = gunler[-2] if len(gunler) > 1 else son
    with np.errstate(divide='ignore', invalid='ignore'):
        df_analiz['Gunluk_Degisim'] = (df_analiz[son] / df_analiz[prev_day]) - 1
    df_analiz['Gunluk_Degisim'] = df_analiz['Gunluk_Degisim'].replace([np.inf, -np.inf], np.nan).fillna(0)

    rap_text = generate_detailed_static_report(df_analiz, son, enf_genel, enf_gida, 0, month_end_forecast, ad_col,
                                               agirlik_col)
    cols = ['Kod', ad_col, 'Grup', 'Gunluk_Degisim', 'Fark'] + gunler
    full_table = df_analiz[cols].rename(columns={ad_col: 'Madde_Adi'}).to_dict('records')

    top_inc = df_analiz.sort_values('Gunluk_Degisim', ascending=False).head(5)[
        ['Kod', ad_col, 'Gunluk_Degisim']].rename(columns={ad_col: 'Madde_Adi'}).to_dict('records')
    top_dec = df_analiz.sort_values('Gunluk_Degisim', ascending=True).head(5)[['Kod', ad_col, 'Gunluk_Degisim']].rename(
        columns={ad_col: 'Madde_Adi'}).to_dict('records')
    heatmap = df_analiz[['Grup', ad_col, agirlik_col, 'Fark']].rename(columns={ad_col: 'Madde_Adi'}).to_dict('records')
    df_resmi = get_official_inflation()

    # Trend verisi (Sadece grafik için boş döndürüyoruz, forecast'i elle yaptık)
    trend_data = []

    print("--- [GITHUB] Islem Tamam ---")
    return {
        "kpi": {"inflation_genel": enf_genel, "inflation_food": enf_gida, "forecast": month_end_forecast,
                "official": df_resmi.iloc[-1]['Resmi_TUFE'], "official_date": "2025-12", "last_update": son},
        "ticker": {"inc": top_inc, "dec": top_dec},
        "charts": {"heatmap": heatmap, "trend": trend_data, "waterfall": waterfall_data},
        "market_depth": {"rising": rising_count, "falling": falling_count},
        "full_table": full_table,
        "report_text": rap_text
    }


def calculate_dashboard_metrics(force_refresh=False):
    global GLOBAL_CACHE
    if GLOBAL_CACHE["data"] is not None and not force_refresh: return GLOBAL_CACHE["data"]
    try:
        data = _internal_calculate_metrics()
        if "error" not in data: GLOBAL_CACHE["data"] = data
        return data
    except Exception as e:
        return {"error": str(e)}


def create_full_pdf():
    data = calculate_dashboard_metrics(force_refresh=False)
    if "error" in data: return b"Hata"
    kpi = data['kpi']
    pdf = PDFReport()
    pdf.create_cover(kpi['last_update'], f"{kpi['inflation_genel']:.2f}")
    pdf.add_page()
    pdf.chapter_title("PIYASA GENEL GORUNUMU")
    pdf.create_kpi_summary(kpi['inflation_genel'], kpi['inflation_food'], data['ticker']['inc'][0]['Madde_Adi'])
    pdf.chapter_title("STRATEJIK ANALIZ")
    pdf.write_markdown(data['report_text'])
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        pdf.output(tmp.name)
        with open(tmp.name, "rb") as f: return f.read()
