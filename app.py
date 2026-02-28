import streamlit as st
import geopandas as gpd
import pandas as pd
import numpy as np
import os
import zipfile
import tempfile
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from shapely.ops import linemerge
from shapely.geometry import LineString, Polygon
import io
import folium
from streamlit_folium import st_folium

# Import untuk ekstraksi DSM
import rasterio
from rasterstats import zonal_stats

# Import untuk ReportLab (PDF)
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import pagesizes
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# =========================================
# KONFIGURASI HALAMAN
# =========================================
st.set_page_config(page_title="GeoSDI System", page_icon="🛣️", layout="wide")

st.title("🛣️ GeoSDI: Sistem Analisis Kondisi Jalan")
st.markdown("Otomatisasi perhitungan Surface Distress Index (SDI) berbagis GIS.")

st.divider()

# =========================================
# INISIALISASI MEMORI (SESSION STATE)
# =========================================
if 'proses_selesai' not in st.session_state:
    st.session_state.proses_selesai = False
if 'df_sdi' not in st.session_state:
    st.session_state.df_sdi = None
if 'peta_bytes' not in st.session_state:
    st.session_state.peta_bytes = None
if 'grafik_bytes' not in st.session_state:
    st.session_state.grafik_bytes = None
if 'pdf_bytes' not in st.session_state:
    st.session_state.pdf_bytes = None
if 'gpkg_bytes' not in st.session_state: 
    st.session_state.gpkg_bytes = None   
if 'seg_gdf' not in st.session_state:
    st.session_state.seg_gdf = None
if 'excel_bytes' not in st.session_state:
    st.session_state.excel_bytes = None

# ==========================================================
# FUNGSI PEMROSESAN SPASIAL & MATEMATIKA
# ==========================================================
def read_zip_shapefile(uploaded_file, tmpdir):
    """Membaca shapefile dari dalam file zip"""
    zip_path = os.path.join(tmpdir, uploaded_file.name)
    with open(zip_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    
    extract_dir = os.path.join(tmpdir, uploaded_file.name.replace('.zip', ''))
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)

    for root, dirs, files in os.walk(extract_dir):
        for file in files:
            if file.endswith(".shp"):
                return gpd.read_file(os.path.join(root, file))
    return None

def hitung_depth_cm(gdf, dsm_path, buffer_distance=0.3):
    """Menghitung kedalaman rutting dari DSM dalam satuan cm"""
    with rasterio.open(dsm_path) as DSM:
        dsm_crs = DSM.crs
        nodata_val = DSM.nodata

    if gdf.crs != dsm_crs:
        gdf = gdf.to_crs(dsm_crs)

    buffer_outer = gdf.geometry.buffer(buffer_distance)
    ring_geom = buffer_outer.difference(gdf.geometry)

    stats_hole = zonal_stats(gdf.geometry, dsm_path, stats=["percentile_10"], nodata=nodata_val)
    stats_ring = zonal_stats(ring_geom, dsm_path, stats=["median"], nodata=nodata_val)

    depth_list = []
    for i in range(len(gdf)):
        z_min = stats_hole[i]["percentile_10"]
        z_ref = stats_ring[i]["median"]
        
        depth = (z_ref - z_min) * 100 if (z_min is not None and z_ref is not None) else 0
        depth = max(0, min(depth, 15)) 
        depth_list.append(depth)

    gdf = gdf.copy()
    gdf["kedalaman_calc"] = depth_list
    return gdf

def hitung_sdi(persen_retak, lebar_retak, jumlah_lubang, kedalaman_rutting):
    # SDI 1 
    if persen_retak == 0: sdi1 = 0
    elif persen_retak < 10: sdi1 = 5
    elif persen_retak <= 30: sdi1 = 20
    else: sdi1 = 40

    # SDI 2 
    sdi2 = sdi1 * 2 if lebar_retak > 3 else sdi1

    # SDI 3 
    if jumlah_lubang == 0: sdi3 = sdi2
    elif jumlah_lubang < 10: sdi3 = sdi2 + 15
    elif jumlah_lubang <= 50: sdi3 = sdi2 + 75
    else: sdi3 = sdi2 + 225

    # SDI 4 
    if kedalaman_rutting == 0: sdi4 = sdi3
    elif kedalaman_rutting < 1: sdi4 = sdi3 + (5 * 0.5)
    elif kedalaman_rutting <= 3: sdi4 = sdi3 + (5 * 2)
    else: sdi4 = sdi3 + (5 * 4)

    # Klasifikasi
    if sdi4 < 50: kondisi = "Baik"
    elif sdi4 <= 100: kondisi = "Sedang"
    elif sdi4 <= 150: kondisi = "Rusak Ringan"
    else: kondisi = "Rusak Berat"
        
    return sdi1, sdi2, sdi3, sdi4, kondisi

# =========================================
# TAMPILAN SIDEBAR
# =========================================
with st.sidebar:
    st.header("📝 Informasi Survey")
    lokasi = st.text_input("Lokasi Survey", "Jl. Leyangan")
    sta_umum = st.text_input("STA Umum", "0+000 - 1+500")
    surveyor = st.text_input("Nama Surveyor", "Nama Anda")
    tanggal = st.text_input("Tanggal Survey", "28 Februari 2026")
    instansi = st.text_input("Instansi", "Universitas Diponegoro")
    
    st.header("⚙️ Parameter Jalan")
    lebar_jalan = st.number_input("Lebar Jalan (m)", value=3.0, step=0.1)
    interval_segmen = st.number_input("Interval Segmen (m)", value=100, step=10)
    epsg_code = st.number_input("Kode EPSG UTM Lokal (Contoh: 32749 untuk Jawa Tengah)", value=32749, step=1)
   
    st.divider()
    if st.button("🔄 Reset / Mulai Ulang Aplikasi", use_container_width=True):
        for key in st.session_state.keys():
            del st.session_state[key]
        st.rerun()

# =========================================
# TAMPILAN UTAMA (UPLOAD FILES)
# =========================================
col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("📁 1. Data Dasar Jalan")
    jalan_file = st.file_uploader("Upload Shapefile Jalan (.zip)", type="zip", key="jalan")
    st.info("💡 Pastikan EPSG sesuai zona UTM wilayah.")

with col2:
    st.subheader("⚠️ 2. Data Kerusakan")
    retak_file = st.file_uploader("SHP Retak (.zip) [Poligon]", type="zip", key="retak")
    pothole_file = st.file_uploader("SHP Lubang (.zip) [Point/Poligon]", type="zip", key="pothole")
    rutting_file = st.file_uploader("SHP Rutting (.zip) [Poligon]", type="zip", key="rutting")

with col3:
    st.subheader("🗺️ 3. DSM untuk Rutting")
    dsm_mode = st.radio("Cara Input Data DSM:", ["Upload File .tif", "Paste Link Google Drive"])
    dsm_file = None
    dsm_link = ""
    
    if dsm_mode == "Upload File .tif":
        dsm_file = st.file_uploader("Upload Data DSM (.tif)", type="tif")
    else:
        dsm_link = st.text_input("Paste Link Shareable Google Drive (.tif)")
        st.caption("Pastikan akses link Google Drive diatur ke 'Anyone with the link'.")

st.divider()

# =========================================
# PROSES UTAMA (EKSEKUSI)
# =========================================
if st.button("🚀 Proses & Hitung SDI", type="primary", use_container_width=True):
    
    is_dsm_valid = False
    if dsm_mode == "Upload File .tif" and dsm_file is not None:
        is_dsm_valid = True
    elif dsm_mode == "Paste Link Google Drive" and dsm_link != "":
        is_dsm_valid = True

    if not jalan_file or not is_dsm_valid:
        st.error("⚠️ Mohon lengkapi Shapefile Jalan dan Data DSM (Upload File / Link) untuk melanjutkan.")
    else:
        with st.spinner("Memproses Analisis Geospasial SDI & Ekstraksi DSM... (Mungkin memakan waktu beberapa saat)"):
            with tempfile.TemporaryDirectory() as tmpdir:
                try:
                    # 1. DOWNLOAD ATAU SIMPAN DSM
                    dsm_path = os.path.join(tmpdir, "dsm.tif")
                    if dsm_mode == "Upload File .tif":
                        with open(dsm_path, "wb") as f:
                            f.write(dsm_file.getbuffer())
                    elif dsm_mode == "Paste Link Google Drive":
                        st.info("⏳ Mengunduh DSM dari Google Drive...")
                        import gdown
                        import re
                        match = re.search(r"/d/([a-zA-Z0-9_-]+)", dsm_link)
                        if match:
                            file_id = match.group(1)
                            gdown.download(id=file_id, output=dsm_path, quiet=False)
                        else:
                            st.error("❌ Link Google Drive tidak valid. Pastikan format link benar.")
                            st.stop()

                    # 2. BACA JALAN & BUAT SEGMEN
                    jalan = read_zip_shapefile(jalan_file, tmpdir)
                    if jalan.crs is None:
                        jalan.set_crs(epsg=4326, inplace=True)
                    if jalan.crs.to_epsg() != epsg_code:
                        jalan = jalan.to_crs(epsg=epsg_code)
                    
                    union_geom = jalan.geometry.union_all()
                    merged_line = linemerge(union_geom) if union_geom.geom_type == "MultiLineString" else union_geom
                    
                    panjang_total = merged_line.length
                    segments = [LineString([merged_line.interpolate(start), merged_line.interpolate(min(start + interval_segmen, panjang_total))]) 
                                for start in np.arange(0, panjang_total, interval_segmen)]
                    
                    seg_gdf = gpd.GeoDataFrame(geometry=segments, crs=jalan.crs)
                    seg_gdf["Segmen"] = range(1, len(seg_gdf)+1)
                    seg_gdf["STA"] = seg_gdf["Segmen"].apply(lambda x: f"{(x-1)*interval_segmen:03.0f}+000 - {min(x*interval_segmen, int(panjang_total)):03.0f}+000")
                    
                    seg_gdf["geometry"] = seg_gdf.buffer(lebar_jalan / 2, cap_style=2)
                    seg_gdf["Luas_Segmen"] = seg_gdf.geometry.area
                    
                    # 3. BACA DATA KERUSAKAN
                    gdf_retak = read_zip_shapefile(retak_file, tmpdir) if retak_file else gpd.GeoDataFrame(columns=['geometry'], crs=seg_gdf.crs)
                    gdf_pothole = read_zip_shapefile(pothole_file, tmpdir) if pothole_file else gpd.GeoDataFrame(columns=['geometry'], crs=seg_gdf.crs)
                    gdf_rutting = read_zip_shapefile(rutting_file, tmpdir) if rutting_file else gpd.GeoDataFrame(columns=['geometry'], crs=seg_gdf.crs)
                    
                    for gdf in [gdf_retak, gdf_pothole, gdf_rutting]:
                        if not gdf.empty:
                            if gdf.crs is None:
                                gdf.set_crs(seg_gdf.crs, inplace=True)
                            elif gdf.crs != seg_gdf.crs:
                                gdf.to_crs(seg_gdf.crs, inplace=True)

                    if not gdf_rutting.empty:
                        gdf_rutting = hitung_depth_cm(gdf_rutting, dsm_path)

                    # 4. KALKULASI OVERLAY & SDI PER SEGMEN
                    hasil_sdi = []
                    
                    for idx, seg in seg_gdf.iterrows():
                        seg_poly = gpd.GeoDataFrame(geometry=[seg.geometry], crs=seg_gdf.crs)
                        luas_seg = seg["Luas_Segmen"]
                        
                        persen_retak = 0.0
                        lebar_retak = 0.0
                        if not gdf_retak.empty:
                            retak_seg = gpd.overlay(gdf_retak, seg_poly, how="intersection")
                            if not retak_seg.empty:
                                luas_retak = retak_seg.geometry.area.sum()
                                persen_retak = (luas_retak / luas_seg) * 100 if luas_seg > 0 else 0
                                lengths = retak_seg.geometry.length
                                valid_lengths = lengths[lengths > 0]
                                if len(valid_lengths) > 0:
                                    retak_seg.loc[lengths > 0, "lebar_calc"] = retak_seg.geometry.area / valid_lengths
                                    lebar_retak = retak_seg["lebar_calc"].mean() * 1000 

                        jumlah_lubang = 0
                        if not gdf_pothole.empty:
                            pothole_seg = gpd.sjoin(gdf_pothole, seg_poly, predicate="within")
                            jumlah_lubang = len(pothole_seg)

                        kedalaman_rutting = 0.0
                        if not gdf_rutting.empty:
                            rutting_seg = gpd.overlay(gdf_rutting, seg_poly, how="intersection")
                            if not rutting_seg.empty:
                                kedalaman_rutting = rutting_seg["kedalaman_calc"].mean()

                        kedalaman_rutting = 0 if pd.isna(kedalaman_rutting) else kedalaman_rutting
                        sdi1, sdi2, sdi3, sdi4, kondisi = hitung_sdi(persen_retak, lebar_retak, jumlah_lubang, kedalaman_rutting)
                        
                        hasil_sdi.append({
                            "Segmen": seg["Segmen"],
                            "%Retak": round(persen_retak, 2),
                            "Lebar Retak (mm)": round(lebar_retak, 2),
                            "Jumlah Lubang": jumlah_lubang,
                            "Rutting (cm)": round(kedalaman_rutting, 2),
                            "SDI1": sdi1, "SDI2": sdi2, "SDI3": sdi3, "SDI4": round(sdi4, 2),
                            "Kondisi": kondisi
                        })

                    # 5. GABUNGKAN KE DATAFRAME
                    df_sdi = pd.DataFrame(hasil_sdi)
                    seg_gdf = seg_gdf.merge(df_sdi, on="Segmen", how="left")
                    
                    # =========================================
                    # VISUALISASI PETA & GRAFIK
                    # =========================================
                    warna_kondisi = {"Baik": "#2ecc71", "Sedang": "#f1c40f", "Rusak Ringan": "#e67e22", "Rusak Berat": "#e74c3c"}
                    
                    fig_map, ax_map = plt.subplots(figsize=(10,6))
                    seg_gdf.boundary.plot(ax=ax_map, linewidth=0.5, color="black")
                    legend_handles = []
                    for kondisi, warna in warna_kondisi.items():
                        subset = seg_gdf[seg_gdf["Kondisi"] == kondisi]
                        if not subset.empty:
                            subset.plot(ax=ax_map, color=warna, edgecolor="black", linewidth=1)
                            legend_handles.append(mpatches.Patch(color=warna, label=f"{kondisi} ({len(subset)})"))
                    
                    for idx, row in seg_gdf.iterrows():
                        centroid = row.geometry.centroid
                        ax_map.text(centroid.x, centroid.y, f"S{row['Segmen']}\n{row['SDI4']:.0f}",
                            fontsize=7, weight="bold", ha="center", va="center",
                            bbox=dict(facecolor="white", alpha=0.8, boxstyle="round,pad=0.2", edgecolor="gray", lw=0.5))
                    
                    if legend_handles:
                        ax_map.legend(handles=legend_handles, loc="best", title="Kategori Kondisi", fontsize=8, title_fontsize=9)
                    ax_map.set_title("Peta Kondisi Jalan Metode SDI", fontsize=12, weight="bold")
                    ax_map.axis("off")
                    peta_path = os.path.join(tmpdir, "peta_sdi.png")
                    plt.savefig(peta_path, dpi=300, bbox_inches='tight')
                    plt.close(fig_map)
                    
                    fig_bar, ax_bar = plt.subplots(figsize=(6,4))
                    rekap = seg_gdf["Kondisi"].value_counts()
                    warna_bar = [warna_kondisi.get(x, "grey") for x in rekap.index]
                    rekap.plot(kind="bar", color=warna_bar, edgecolor="black", ax=ax_bar)
                    plt.title("Distribusi Kondisi Jalan (Segmen)")
                    plt.xticks(rotation=0)
                    plt.tight_layout()
                    grafik_path = os.path.join(tmpdir, "grafik_sdi.png")
                    plt.savefig(grafik_path, dpi=300)
                    plt.close(fig_bar)
                    
                    # =========================================
                    # PEMBUATAN PDF (REPORTLAB)
                    # =========================================
                    pdf_path = os.path.join(tmpdir, "Laporan_SDI.pdf")
                    doc = SimpleDocTemplate(pdf_path, pagesize=pagesizes.A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
                    elements = []
                    styles = getSampleStyleSheet()
                    cover_style = ParagraphStyle('cover', parent=styles['Title'], alignment=TA_CENTER)
                    rata_sdi = round(df_sdi["SDI4"].mean(), 2)
                    kondisi_dominan = df_sdi["Kondisi"].value_counts().idxmax() if not df_sdi.empty else "-"

                    elements.append(Paragraph(instansi, cover_style))
                    elements.append(Spacer(1, 0.3*inch))
                    elements.append(Paragraph("LAPORAN SURVEY", cover_style))
                    elements.append(Spacer(1, 0.3*inch))
                    elements.append(Paragraph("SURFACE DISTRESS INDEX (SDI)", cover_style))
                    elements.append(Spacer(1, 1*inch))
                    elements.append(Paragraph(f"<b>Lokasi :</b> {lokasi}", styles["Normal"]))
                    elements.append(Paragraph(f"<b>STA :</b> {sta_umum}", styles["Normal"]))
                    elements.append(Paragraph(f"<b>Surveyor :</b> {surveyor}", styles["Normal"]))
                    elements.append(Paragraph(f"<b>Tanggal :</b> {tanggal}", styles["Normal"]))
                    elements.append(PageBreak())

                    elements.append(Paragraph("<b>1. Ringkasan Rekapitulasi Umum</b>", styles["Heading2"]))
                    ringkasan_table = Table([
                        ["Lokasi", lokasi], ["STA", sta_umum], 
                        ["Jumlah Segmen", str(len(seg_gdf))],
                        ["Panjang Jalan Terukur", f"{len(seg_gdf)*interval_segmen} meter"],
                        ["Rata-rata SDI Keseluruhan", f"{rata_sdi}"], 
                        ["Kondisi Dominan", kondisi_dominan]
                    ], colWidths=[200, 300])
                    ringkasan_table.setStyle(TableStyle([
                        ('GRID',(0,0),(-1,-1),0.5,colors.grey), 
                        ('BACKGROUND',(0,0),(0,-1),colors.HexColor("#f3f4f6")), 
                        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
                        ('PADDING', (0,0), (-1,-1), 8)
                    ]))
                    elements.append(ringkasan_table)
                    elements.append(Spacer(1, 0.3 * inch))

                    elements.append(Paragraph("<b>2. Visualisasi Kondisi Jalan</b>", styles["Heading2"]))
                    elements.append(Image(peta_path, width=7.5*inch, height=4.5*inch))
                    elements.append(Spacer(1, 0.2 * inch))
                    elements.append(Image(grafik_path, width=4.5*inch, height=3*inch))
                    elements.append(PageBreak())

                    # --- TABEL 3: DATA KERUSAKAN TERUKUR ---
                    elements.append(Paragraph("<b>3. Data Kerusakan Terukur Per Segmen</b>", styles["Heading2"]))
                    elements.append(Spacer(1, 0.2 * inch))
                    
                    tabel1_data = [["Segmen", "STA", "% Retak", "Lebar Retak\n(mm)", "Jumlah\nLubang", "Rutting\n(cm)"]]
                    for _, row in df_sdi.iterrows():
                        sta_val = seg_gdf[seg_gdf["Segmen"] == row["Segmen"]].iloc[0]["STA"]
                        tabel1_data.append([
                            str(row["Segmen"]), sta_val, str(row["%Retak"]), str(row["Lebar Retak (mm)"]), 
                            str(row["Jumlah Lubang"]), str(row["Rutting (cm)"])
                        ])
                        
                    t1_detail = Table(tabel1_data, repeatRows=1, colWidths=[0.8*inch, 2.0*inch, 1.0*inch, 1.2*inch, 1.0*inch, 1.0*inch])
                    t1_detail.setStyle(TableStyle([
                        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1e293b")),
                        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0,0), (-1,-1), 9),
                        ('PADDING', (0,0), (-1,-1), 6)
                    ]))
                    elements.append(t1_detail)
                    elements.append(PageBreak())

                    # --- TABEL 4: PERHITUNGAN BERJENJANG SDI ---
                    elements.append(Paragraph("<b>4. Perhitungan Berjenjang SDI Per Segmen</b>", styles["Heading2"]))
                    elements.append(Spacer(1, 0.2 * inch))
                    
                    tabel2_data = [["Segmen", "STA", "SDI 1\n(Retak)", "SDI 2\n(+L. Retak)", "SDI 3\n(+Lubang)", "SDI 4\n(+Rutting)", "Kondisi Akhir"]]
                    for _, row in df_sdi.iterrows():
                        sta_val = seg_gdf[seg_gdf["Segmen"] == row["Segmen"]].iloc[0]["STA"]
                        tabel2_data.append([
                            str(row["Segmen"]), sta_val, str(row["SDI1"]), str(row["SDI2"]), 
                            str(row["SDI3"]), str(row["SDI4"]), row["Kondisi"]
                        ])
                        
                    t2_detail = Table(tabel2_data, repeatRows=1, colWidths=[0.8*inch, 1.8*inch, 0.8*inch, 0.9*inch, 0.8*inch, 0.8*inch, 1.1*inch])
                    t2_detail.setStyle(TableStyle([
                        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1e293b")),
                        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0,0), (-1,-1), 9),
                        ('PADDING', (0,0), (-1,-1), 6)
                    ]))
                    elements.append(t2_detail)
                    
                    doc.build(elements)

                    # =========================================
                    # PEMBUATAN FILE SPASIAL & EXCEL
                    # =========================================
                    gpkg_path = os.path.join(tmpdir, "Peta_Hasil_SDI.gpkg")
                    export_gdf = seg_gdf.copy()
                    for col in export_gdf.columns:
                        if export_gdf[col].apply(lambda x: isinstance(x, (list, tuple))).any():
                            export_gdf[col] = export_gdf[col].astype(str)
                    export_gdf.to_file(gpkg_path, driver="GPKG")

                    excel_buffer = io.BytesIO()
                    with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                        df_sdi.to_excel(writer, sheet_name='Rekap SDI', index=False)
                    excel_bytes = excel_buffer.getvalue()

                    # =========================================
                    # SIMPAN KE SESSION STATE
                    # =========================================
                    st.session_state.df_sdi = df_sdi
                    st.session_state.seg_gdf = seg_gdf           
                    st.session_state.excel_bytes = excel_bytes   
                    
                    with open(peta_path, "rb") as f: st.session_state.peta_bytes = f.read()
                    with open(grafik_path, "rb") as f: st.session_state.grafik_bytes = f.read()
                    with open(pdf_path, "rb") as f: st.session_state.pdf_bytes = f.read()
                    with open(gpkg_path, "rb") as f: st.session_state.gpkg_bytes = f.read()       
                        
                    st.session_state.proses_selesai = True

                except Exception as e:
                    st.error(f"❌ Terjadi kesalahan saat memproses data: {e}")
                    st.session_state.proses_selesai = False

# =========================================
# TAMPILKAN HASIL DI WEB
# =========================================
if st.session_state.proses_selesai:
    st.success("✅ Analisis SDI Berhasil!")
    
    col_res1, col_res2 = st.columns([2, 1])
    with col_res1:
        st.subheader("🗺️ Peta Kondisi SDI")
        
        if st.session_state.seg_gdf is not None:
            map_gdf = st.session_state.seg_gdf.to_crs(epsg=4326)
            center_y = map_gdf.geometry.centroid.y.mean()
            center_x = map_gdf.geometry.centroid.x.mean()
            
            m = folium.Map(location=[center_y, center_x], zoom_start=15, tiles="CartoDB positron")
            warna_kondisi_dict = {"Baik": "#2ecc71", "Sedang": "#f1c40f", "Rusak Ringan": "#e67e22", "Rusak Berat": "#e74c3c"}
            
            folium.GeoJson(
                map_gdf,
                style_function=lambda feature: {
                    'fillColor': warna_kondisi_dict.get(feature['properties']['Kondisi'], "#000000"),
                    'color': 'black',
                    'weight': 1,
                    'fillOpacity': 0.8,
                },
                tooltip=folium.features.GeoJsonTooltip(
                    fields=['Segmen', 'STA', 'SDI4', 'Kondisi'], 
                    aliases=['Segmen:', 'STA:', 'Nilai SDI:', 'Kondisi:'],
                    style="font-family: Arial; font-size: 12px; padding: 5px;"
                )
            ).add_to(m)
            st_folium(m, use_container_width=True, height=400)
            
    with col_res2:
        st.subheader("Distribusi")
        st.image(st.session_state.grafik_bytes)
        st.metric("Rata-rata Nilai SDI", round(st.session_state.df_sdi["SDI4"].mean(), 2))
    
    st.markdown("---")
    
    col_tab, col_leg = st.columns([2, 1])
    with col_tab:
        st.subheader("Tabel Rekapitulasi Kondisi")
        display_df = st.session_state.seg_gdf[["Segmen", "STA", "SDI4", "Kondisi"]].copy()
        display_df.rename(columns={"SDI4": "Nilai SDI"}, inplace=True)
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    with col_leg:
        st.markdown("<h4 style='text-align: center; margin-bottom: 15px;'>Skala Rating SDI</h4>", unsafe_allow_html=True)
        skala_sdi = [
            ("Baik", "#2ecc71", "white", "< 50"),
            ("Sedang", "#f1c40f", "black", "50 - 100"),
            ("Rusak Ringan", "#e67e22", "white", "101 - 150"),
            ("Rusak Berat", "#e74c3c", "white", "> 150")
        ]
        for nama, bg, txt, rentang in skala_sdi:
            html_baris = f"<div style='background-color: {bg}; color: {txt}; padding: 10px; margin-bottom: 5px; border-radius: 5px; display: flex; justify-content: space-between; font-weight: bold;'><span>{nama}</span><span>{rentang}</span></div>"
            st.markdown(html_baris, unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("🔎 Dashboard Detail Perhitungan Segmen")
    st.markdown("Pilih nomor segmen di bawah ini untuk melihat rincian perhitungan Indeks SDI berjenjang.")

    list_segmen = st.session_state.df_sdi["Segmen"].tolist()
    pilihan_segmen = st.selectbox("Pilih Segmen:", list_segmen)

    if pilihan_segmen:
        df_sdi_mem = st.session_state.df_sdi
        seg_data = df_sdi_mem[df_sdi_mem["Segmen"] == pilihan_segmen].iloc[0]
        sta_display = st.session_state.seg_gdf[st.session_state.seg_gdf["Segmen"] == pilihan_segmen].iloc[0]["STA"]

        st.markdown(f"#### REPORT SEGMEN : {pilihan_segmen} (STA: {sta_display})")

        def metric_card(label, value, value_color="#4da6ff", bg_color="#1E2A38", text_color="#cbd5e1"):
            return f'<div style="background-color: {bg_color}; padding: 15px; border-radius: 8px; border: 1px solid #2d3e50; text-align: center; height: 100%;"><p style="margin: 0px; font-size: 14px; color: {text_color};">{label}</p><h2 style="margin: 5px 0px 0px 0px; color: {value_color}; font-size: 22px; font-weight: bold;">{value}</h2></div>'

        st.markdown("**A. Data Kerusakan Terukur**")
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        with col_m1: st.markdown(metric_card("Luas Retak (%)", f"{seg_data['%Retak']:.2f}%"), unsafe_allow_html=True)
        with col_m2: st.markdown(metric_card("Lebar Retak (mm)", f"{seg_data['Lebar Retak (mm)']:.2f}"), unsafe_allow_html=True)
        with col_m3: st.markdown(metric_card("Jumlah Lubang (Ttk)", f"{seg_data['Jumlah Lubang']}"), unsafe_allow_html=True)
        with col_m4: st.markdown(metric_card("Rutting/Alur (cm)", f"{seg_data['Rutting (cm)']:.2f}"), unsafe_allow_html=True)

        st.markdown("<br>**B. Perhitungan Berjenjang SDI**", unsafe_allow_html=True)
        col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns(5)
        with col_s1: st.markdown(metric_card("SDI 1<br>(Retak)", f"{seg_data['SDI1']}"), unsafe_allow_html=True)
        with col_s2: st.markdown(metric_card("SDI 2<br>(+Lebar Retak)", f"{seg_data['SDI2']}"), unsafe_allow_html=True)
        with col_s3: st.markdown(metric_card("SDI 3<br>(+Lubang)", f"{seg_data['SDI3']}"), unsafe_allow_html=True)
        with col_s4: st.markdown(metric_card("SDI 4<br>(+Rutting)", f"{seg_data['SDI4']:.2f}", value_color="#ffcc00"), unsafe_allow_html=True)
        
        warna_kondisi_dict = {"Baik": "#2ecc71", "Sedang": "#f1c40f", "Rusak Ringan": "#e67e22", "Rusak Berat": "#e74c3c"}
        bg_col = warna_kondisi_dict.get(seg_data['Kondisi'], "#FFFFFF")
        txt_col = "#000000" if seg_data['Kondisi'] in ["Sedang", "Baik"] else "#ffffff"
        with col_s5: st.markdown(metric_card("Kondisi<br>Akhir", seg_data['Kondisi'], value_color=txt_col, bg_color=bg_col, text_color=txt_col), unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("💾 Download Hasil Analisis")
    
    col_dl1, col_dl2, col_dl3 = st.columns(3)
    with col_dl1:
        st.download_button(
            label="📄 Laporan Full PDF", data=st.session_state.pdf_bytes,
            file_name=f"Laporan_SDI_{lokasi.replace(' ', '_')}.pdf", mime="application/pdf",
            type="primary", use_container_width=True
        )
    with col_dl2:
        st.download_button(
            label="🗺️ Peta Spasial (.gpkg)", data=st.session_state.gpkg_bytes,
            file_name=f"Peta_SDI_{lokasi.replace(' ', '_')}.gpkg", mime="application/geopackage+sqlite3",
            type="secondary", use_container_width=True
        )
    with col_dl3:
        st.download_button(
            label="📊 Data Mentah (.xlsx)", data=st.session_state.excel_bytes,
            file_name=f"Data_SDI_{lokasi.replace(' ', '_')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="secondary", use_container_width=True
        )
