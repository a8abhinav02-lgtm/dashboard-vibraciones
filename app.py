import streamlit as st
import pandas as pd
import io
import re
import xlsxwriter
import traceback

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Dashboard Vibraciones", page_icon="⚙️", layout="wide")

# ==========================================
# 1. FUNCIONES MATEMÁTICAS Y DE TRANSFORMACIÓN
# ==========================================
def calcular_criticidad(var_porcentual):
    if var_porcentual == '---' or pd.isnull(var_porcentual): return '---'
    try:
        val = float(var_porcentual)
        if val >= 50.0: return 'ALTO'
        elif 20.0 <= val < 50.0: return 'MEDIA'
        elif -50.0 <= val < 20.0: return 'NORMAL'
        else: return 'REVISAR'
    except ValueError: return '---'

def procesar_unidad(df_in, month_order, latest_month, group_keys, unidad):
    if df_in.empty: return None
    
    if 'Month' in df_in.columns and 'Value' in df_in.columns:
        df_in = df_in.groupby(group_keys + ['Month'])['Value'].max().reset_index()
    
    pivot_table = df_in.pivot_table(values='Value', index=group_keys, columns='Month', aggfunc='first').reset_index()
    for month in month_order:
        if month not in pivot_table.columns: pivot_table[month] = '---'
    pivot_table = pivot_table[group_keys + month_order].astype(object).fillna('---')
    if not latest_month: return pivot_table

    previous_data = df_in[df_in['Month'] != latest_month]
    if previous_data.empty: return pivot_table 
        
    stats = previous_data.groupby(group_keys)['Value'].agg(['mean', 'std']).reset_index()
    stats[f'Avg {unidad}'] = stats['mean'].round(3)
    stats[f'Std {unidad}'] = stats['std'].round(3)
    
    previous_stats = previous_data.groupby(group_keys)['Value'].mean().reset_index().rename(columns={'Value': 'Previous Avg'})
    median_stats = previous_data.groupby(group_keys)['Value'].median().reset_index().rename(columns={'Value': 'Previous Median'})
    
    stats = stats.merge(previous_stats, on=group_keys, how='left')
    stats = stats.merge(median_stats, on=group_keys, how='left')
    stats['Previous Median'] = stats['Previous Median'].fillna('---')
    stats['Previous Avg'] = stats['Previous Avg'].fillna('---')
    
    latest_values = df_in[df_in['Month'] == latest_month][group_keys + ['Value']].rename(columns={'Value': 'Latest Value'})
    stats = stats.merge(latest_values, on=group_keys, how='left')
    
    stats['Latest vs Median'] = stats.apply(lambda x: round(x['Latest Value'] - x['Previous Median'], 3) if pd.notnull(x['Latest Value']) and x['Previous Median'] != '---' else '---', axis=1)
    stats['Latest vs Avg'] = stats.apply(lambda x: round(x['Latest Value'] - x['Previous Avg'], 3) if pd.notnull(x['Latest Value']) and x['Previous Avg'] != '---' else '---', axis=1)
    
    stats['Var % vs Avg'] = stats.apply(
        lambda x: round(((x['Latest Value'] - x['Previous Avg']) / x['Previous Avg']) * 100, 2) 
        if pd.notnull(x['Latest Value']) and x['Previous Avg'] not in ['---', 0, 0.0] else '---', axis=1
    )
    stats['Criticality'] = stats['Var % vs Avg'].apply(calcular_criticidad)
    
    cols_stats = [f'Avg {unidad}', f'Std {unidad}', 'Latest vs Avg', 'Var % vs Avg', 'Latest vs Median', 'Criticality']
    stats_final = stats[group_keys + cols_stats]
    pivot_table_final = pivot_table.merge(stats_final, on=group_keys, how='left')
    pivot_table_final = pivot_table_final[group_keys + cols_stats + month_order]
    
    cols_to_format = month_order + [f'Avg {unidad}', f'Std {unidad}', 'Latest vs Avg', 'Var % vs Avg', 'Latest vs Median']
    for col in cols_to_format:
        if col in pivot_table_final.columns:
            pivot_table_final[col] = pivot_table_final[col].apply(lambda x: f"{float(x):.3f}" if isinstance(x, (int, float)) and pd.notnull(x) else x)
    return pivot_table_final

def obtener_lista_negra_df(df):
    if df is None or df.empty: return pd.DataFrame()
    df_alertas = df[df['Criticality'].isin(['ALTO', 'MEDIA'])].copy()
    if df_alertas.empty: return pd.DataFrame()
    
    df_alertas['Ord_Var'] = pd.to_numeric(df_alertas['Var % vs Avg'], errors='coerce').fillna(0)
    df_alertas['Crit_Rank'] = df_alertas['Criticality'].map({'ALTO': 1, 'MEDIA': 2})
    df_alertas = df_alertas.sort_values(by=['Crit_Rank', 'Ord_Var'], ascending=[True, False])
    
    df_alertas['Var % vs Avg'] = df_alertas['Var % vs Avg'].apply(lambda x: f"{x}%" if x != '---' else x)
    return df_alertas[['Area', 'Equipment', 'Unit', 'Criticality', 'Var % vs Avg', 'Latest vs Avg']]


# ==========================================
# 2. PARSERS DE EXTRACCIÓN
# ==========================================
def detectar_tipo_archivo(lines):
    texto_muestra = " ".join(lines[:150]) 
    if re.search(r'\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}', texto_muestra): return 'equipo'
    if re.search(r'[A-Z][a-z]{2},\s\d{4}', texto_muestra): return 'maquina'
    return 'unknown'

def parse_maquinas(lines):
    data, current_area, current_equipment, current_unit = [], None, None, None
    meses_validos = ['Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov']

    for row_str in lines:
        row_str = row_str.strip()
        if not row_str: continue
        
        if row_str.startswith('Area:'): 
            current_area = row_str.split('Area:')[1].strip()
            continue
            
        if row_str.startswith(('PP', 'PF', 'PC', 'PR')) and not row_str.startswith('Database:') and not row_str.startswith('Report Date:'):
            current_equipment = row_str.strip()
            continue

        if 'mm/Sec' in row_str: 
            current_unit = 'mm/Sec RMS'
            continue
            
        if 'G-s' in row_str: 
            current_unit = 'G-s RMS'
            continue

        if current_unit in ['mm/Sec RMS', 'G-s RMS'] and any(month in row_str for month in meses_validos):
            parts = row_str.split()
            if len(parts) >= 2:
                month = parts[0].replace(',', '').strip().capitalize()
                year = parts[1].strip()
                val_str = parts[2] if len(parts) > 2 else '-------'
                
                try:
                    val = float(val_str) if val_str != '-------' else None
                except ValueError:
                    val = None
                    
                data.append({'Area': current_area, 'Equipment': current_equipment, 'Unit': current_unit, 'Month': f"{month} {year}", 'Value': val})
                
    return pd.DataFrame(data)

def parse_equipos(lines):
    data, current_area, current_equipment, current_tag, current_unit = [], None, None, None, None
    meses_nombres = {1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun', 7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec'}
    ruido = ['Measurement Point History', 'Database:', 'Report Date:', 'Period Reported:', 'Baseline Value', 'Early Warning Limit', 'Alert Limit Value', 'Fault Limit Value', 'Calc. Mean Value', 'Standard Deviation', '*************************']

    for row_str in lines:
        row_str = row_str.strip()
        if not row_str or set(row_str) == {'-'}: continue 
        if any(row_str.startswith(r) for r in ruido) or any(r in row_str for r in ruido): continue
            
        if row_str.startswith('Area:'): current_area = row_str.split('Area:')[1].strip(); continue
        if row_str.startswith('Equipment '):
            parts = row_str.split(':', 1)
            if len(parts) > 1: current_equipment = parts[1].strip()
            continue

        if 'mm/Sec' in row_str: current_unit = 'mm/Sec RMS'; continue
        if 'G-s' in row_str: current_unit = 'G-s RMS'; continue

        parts = row_str.split()
        if len(parts) >= 3 and '/' in parts[0] and ':' in parts[1]:
            try:
                dt = pd.to_datetime(parts[0], format='%d/%m/%y')
                month_year = f"{meses_nombres[dt.month]} {dt.year}"
                val = float(parts[2])
                data.append({'Area': current_area, 'Equipment': current_equipment, 'Tag': current_tag, 'Unit': current_unit, 'Month': month_year, 'Value': val})
            except ValueError: pass
            continue
        current_tag = row_str

    df_equipos = pd.DataFrame(data)
    if df_equipos.empty: return df_equipos

    df_equipos = df_equipos.groupby(['Area', 'Equipment', 'Tag', 'Unit', 'Month'])['Value'].max().reset_index()
    df_equipos['Date_obj'] = pd.to_datetime(df_equipos['Month'], format='%b %Y')
    
    def extraer_grupo(tag):
        if '-' in str(tag):
            match = re.search(r'\d+', str(tag).split('-', 1)[1])
            if match: return match.group()
        return str(tag).strip()
    df_equipos['Grupo'] = df_equipos['Tag'].apply(extraer_grupo)

    fechas_recientes = df_equipos.groupby(['Area', 'Equipment', 'Unit', 'Grupo'])['Date_obj'].max().reset_index()
    df_recientes = df_equipos.merge(fechas_recientes, on=['Area', 'Equipment', 'Unit', 'Grupo', 'Date_obj'])
    df_peores = df_recientes.sort_values('Value', ascending=False).drop_duplicates(subset=['Area', 'Equipment', 'Unit', 'Grupo'])
    peores_tags = df_peores[['Area', 'Equipment', 'Unit', 'Grupo', 'Tag']]

    df_final = df_equipos.merge(peores_tags, on=['Area', 'Equipment', 'Unit', 'Grupo', 'Tag'], how='inner')
    df_final['Equipment'] = df_final['Equipment'] + " | " + df_final['Tag']
    return df_final.drop(columns=['Tag', 'Date_obj', 'Grupo'])


# ==========================================
# 3. GENERADOR DE EXCEL
# ==========================================
def generar_excel(tabla_vel, tabla_acc, month_order):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        formato_alto = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006'})
        formato_media = workbook.add_format({'bg_color': '#FFEB9C', 'font_color': '#9C6500'})
        formato_revisar = workbook.add_format({'bg_color': '#E0E0E0', 'font_color': '#000000'})
        fmt_title = workbook.add_format({'bold': True, 'font_size': 14, 'bg_color': '#1F497D', 'font_color': 'white', 'align': 'center'})
        fmt_header = workbook.add_format({'bold': True, 'bg_color': '#DCE6F1', 'border': 1})
        fmt_border = workbook.add_format({'border': 1})
        
        ws_dash = workbook.add_worksheet('Dashboard')
        ws_dash.set_column('A:B', 20); ws_dash.set_column('C:G', 22)
        ws_dash.merge_range('A1:G1', 'DASHBOARD EJECUTIVO - ESTADO DE VIBRACIONES', fmt_title)
        
        def crear_bloque_kpi(df, titulo, fila_inicio):
            if df is None or df.empty: return fila_inicio
            altos, medias = len(df[df['Criticality'] == 'ALTO']), len(df[df['Criticality'] == 'MEDIA'])
            normales, revisar = len(df[df['Criticality'] == 'NORMAL']), len(df[df['Criticality'] == 'REVISAR'])
            
            ws_dash.write(fila_inicio, 0, f"KPIs: {titulo}", fmt_header)
            for i, (est, cant, fmt) in enumerate([('ALTO', altos, formato_alto), ('MEDIA', medias, formato_media), ('NORMAL', normales, fmt_border), ('REVISAR', revisar, formato_revisar)]):
                ws_dash.write(fila_inicio+1+i, 0, est, fmt); ws_dash.write(fila_inicio+1+i, 1, cant, fmt_border)
            ws_dash.write(fila_inicio+6, 0, 'Total', fmt_header); ws_dash.write(fila_inicio+6, 1, len(df), fmt_header)
            
            chart = workbook.add_chart({'type': 'pie'})
            chart.add_series({'name': f'Salud {titulo}', 'categories': ['Dashboard', fila_inicio+1, 0, fila_inicio+4, 0], 'values': ['Dashboard', fila_inicio+1, 1, fila_inicio+4, 1], 'points': [{'fill': {'color': '#FF0000'}}, {'fill': {'color': '#FFC000'}}, {'fill': {'color': '#92D050'}}, {'fill': {'color': '#A6A6A6'}}]})
            chart.set_title({'name': f'Distribución {titulo}'})
            ws_dash.insert_chart(fila_inicio, 3, chart, {'x_scale': 0.8, 'y_scale': 0.8})
            return fila_inicio + 15

        current_row = crear_bloque_kpi(tabla_vel, 'Velocidad', 2)
        current_row = crear_bloque_kpi(tabla_acc, 'Aceleración', current_row)
        
        current_row += 1
        ws_dash.merge_range(f'A{current_row}:G{current_row}', 'LISTA NEGRA PRIORIZADA (ALTO Y MEDIA)', fmt_title)
        current_row += 2
        
        def escribir_lista_negra(df, fila):
            df_alertas = obtener_lista_negra_df(df)
            if df_alertas.empty: return fila
            for col_num, col_name in enumerate(df_alertas.columns): ws_dash.write(fila, col_num, col_name, fmt_header)
            fila += 1
            for _, row in df_alertas.iterrows():
                for i, col in enumerate(['Area', 'Equipment', 'Unit']): ws_dash.write(fila, i, row[col], fmt_border)
                ws_dash.write(fila, 3, row['Criticality'], formato_alto if row['Criticality'] == 'ALTO' else formato_media)
                ws_dash.write(fila, 4, row['Var % vs Avg'], fmt_border)
                ws_dash.write(fila, 5, row['Latest vs Avg'], fmt_border)
                fila += 1
            return fila + 2

        current_row = escribir_lista_negra(tabla_vel, current_row)
        escribir_lista_negra(tabla_acc, current_row)

        def aplicar_formatos(df_tabla, nombre_hoja):
            if df_tabla is None or df_tabla.empty: return
            df_tabla.to_excel(writer, sheet_name=nombre_hoja, index=False)
            worksheet = writer.sheets[nombre_hoja]
            max_row, max_col = df_tabla.shape
            worksheet.add_table(0, 0, max_row, max_col - 1, {'columns': [{'header': col} for col in df_tabla.columns], 'autofilter': True, 'style': 'Table Style Medium 9'})
            if 'Criticality' in df_tabla.columns:
                col_crit = df_tabla.columns.get_loc('Criticality')
                rango = xlsxwriter.utility.xl_range(1, col_crit, max_row, col_crit)
                worksheet.conditional_format(rango, {'type': 'cell', 'criteria': '==', 'value': '"ALTO"', 'format': formato_alto})
                worksheet.conditional_format(rango, {'type': 'cell', 'criteria': '==', 'value': '"MEDIA"', 'format': formato_media})
                worksheet.conditional_format(rango, {'type': 'cell', 'criteria': '==', 'value': '"REVISAR"', 'format': formato_revisar})

        aplicar_formatos(tabla_vel, 'Velocidad')
        aplicar_formatos(tabla_acc, 'Aceleracion')
    return output


# ==========================================
# 4. INTERFAZ GRÁFICA (UI)
# ==========================================

st.markdown("""
    <style>
    .main-title { font-size: 2.8rem; font-weight: 800; color: #1F497D; margin-bottom: 0px; }
    .sub-title { font-size: 1.2rem; color: #555555; margin-top: -10px; margin-bottom: 30px; }
    div.stButton > button {
        background: linear-gradient(90deg, #1F497D 0%, #3B82F6 100%);
        color: white; border: none; border-radius: 6px; font-weight: bold;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1); transition: all 0.3s ease;
    }
    div.stButton > button:hover { transform: translateY(-2px); box-shadow: 0 6px 12px rgba(0,0,0,0.2); color: white; }
    div[data-testid="metric-container"] {
        background-color: white; border: 1px solid #e0e0e0; padding: 15px;
        border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    </style>
    """, unsafe_allow_html=True)

st.markdown('<p class="main-title">⚙️ Plataforma ETL - Mantenimiento Predictivo</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-title">Motor de Análisis y Generación Automática de Dashboards</p>', unsafe_allow_html=True)
st.divider()

tab_maquinas, tab_equipos = st.tabs(["📁 MODO RESUMIDO (Máquinas)", "⚙️ MODO DETALLADO (Equipos por Puntos)"])

def procesar_interfaz(uploaded_file, modo_esperado):
    lines = uploaded_file.getvalue().decode("utf-8", errors="ignore").splitlines()
    
    # --- CONSOLA DE DEPURACIÓN EN PANTALLA ---
    with st.expander("🛠️ CONSOLA DE DEPURACIÓN (Haz clic para ver Logs)", expanded=True):
        st.write(f"**1. Archivo Leído:** {uploaded_file.name} | **Líneas:** {len(lines)}")
        tipo_detectado = detectar_tipo_archivo(lines)
        st.write(f"**2. Tipo Detectado Automáticamente:** {tipo_detectado} | **Modo Solicitado:** {modo_esperado}")
    
    if tipo_detectado != modo_esperado and tipo_detectado != 'unknown':
        if modo_esperado == 'maquina':
            st.error("❌ **¡Archivo Incorrecto!** Detectaste un reporte detallado. Ve a la pestaña **'⚙️ MODO DETALLADO'**.")
        else:
            st.error("❌ **¡Archivo Incorrecto!** Detectaste un reporte resumido. Ve a la pestaña **'📁 MODO RESUMIDO'**.")
        return

    with st.spinner('Procesando datos y calculando tendencias...'):
        try:
            if modo_esperado == 'maquina': df_data = parse_maquinas(lines)
            else: df_data = parse_equipos(lines)

            # --- LOG: MOSTRAR DATA EXTRAÍDA ---
            with st.expander("🛠️ CONSOLA DE DEPURACIÓN (Haz clic para ver Logs)", expanded=True):
                st.write(f"**3. Filas extraídas por el Parser:** {len(df_data)}")
                if not df_data.empty:
                    st.dataframe(df_data.head(10)) # Muestra las primeras 10 filas para confirmar
                else:
                    st.error("CRÍTICO: El parser no encontró ninguna fila válida. Revisa los filtros.")

            if df_data.empty:
                st.warning("No se encontraron datos válidos en el archivo. Revisa el formato.")
                return

            month_order = sorted(df_data['Month'].unique(), key=lambda x: pd.to_datetime(x, format='%b %Y'))
            latest_month = month_order[-1] if month_order else None
            group_keys = ['Area', 'Equipment', 'Unit']

            tabla_vel = procesar_unidad(df_data[df_data['Unit'] == 'mm/Sec RMS'], month_order, latest_month, group_keys, 'mm/Sec RMS')
            tabla_acc = procesar_unidad(df_data[df_data['Unit'] == 'G-s RMS'], month_order, latest_month, group_keys, 'G-s RMS')

            lista_negra_vel = obtener_lista_negra_df(tabla_vel)
            lista_negra_acc = obtener_lista_negra_df(tabla_acc)
            lista_negra_total = pd.concat([lista_negra_vel, lista_negra_acc]).reset_index(drop=True)

            # --- LOG: VERIFICACIÓN FINAL MATEMÁTICA ---
            with st.expander("🛠️ CONSOLA DE DEPURACIÓN (Haz clic para ver Logs)", expanded=True):
                st.write(f"**4. Meses detectados para Pivot:** {month_order}")
                st.write(f"**5. Alertas Generadas en Lista Negra:** {len(lista_negra_total)}")

            st.markdown("### 📊 Panel de Resultados")
            if not lista_negra_total.empty:
                altos = len(lista_negra_total[lista_negra_total['Criticality'] == 'ALTO'])
                medias = len(lista_negra_total[lista_negra_total['Criticality'] == 'MEDIA'])
                
                col1, col2, col3 = st.columns(3)
                col1.metric("🔴 Equipos en ALTO", altos)
                col2.metric("🟡 Equipos en MEDIA", medias)
                col3.metric("⚠️ Total de Alertas", altos + medias)
                
                st.write("")
                def color_criticidad(val):
                    color = '#FFC7CE' if val == 'ALTO' else '#FFEB9C' if val == 'MEDIA' else ''
                    font = '#9C0006' if val == 'ALTO' else '#9C6500' if val == 'MEDIA' else ''
                    return f'background-color: {color}; color: {font}; font-weight: bold' if color else ''

                st.dataframe(lista_negra_total.style.map(color_criticidad, subset=['Criticality']), use_container_width=True, hide_index=True)
            else:
                st.success("✅ ¡Excelente! La planta está estable. No hay equipos en estado ALTO o MEDIA.")

            st.write("")
            excel_file = generar_excel(tabla_vel, tabla_acc, month_order)
            
            with st.container(border=True):
                st.markdown("#### 📥 Exportar Resultados")
                st.download_button(
                    label="Descargar Dashboard en Excel (.xlsx)",
                    data=excel_file.getvalue(),
                    file_name=f"Reporte_Vibraciones_{modo_esperado.capitalize()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

        except Exception as e:
            st.error("🚨 LA APLICACIÓN HA FALLADO 🚨")
            st.error(f"Mensaje de error: {str(e)}")
            st.warning("Copia el siguiente recuadro rojo y compártelo para identificar la falla exacta:")
            st.code(traceback.format_exc(), language='python')

with tab_maquinas:
    with st.expander("📖 ¿Cómo funciona el Modo Resumido?", expanded=False):
        st.write("Ideal para reportes como `feb.txt` que contienen un resumen mensual directo de la máquina, sin desglosar rodamientos específicos.")
    
    with st.container(border=True):
        st.markdown("#### 📤 Carga de Datos")
        col_file, col_btn = st.columns([3, 1])
        with col_file:
            file_maq = st.file_uploader("Selecciona el archivo de texto", type=['txt'], key="maq", label_visibility="collapsed")
        with col_btn:
            if file_maq:
                st.button("⚙️ Analizar", key="btn_maq", use_container_width=True, on_click=lambda: None)
        
        if file_maq:
            st.divider()
            procesar_interfaz(file_maq, 'maquina')

with tab_equipos:
    with st.expander("📖 ¿Cómo funciona el Modo Detallado?", expanded=False):
        st.write("Ideal para reportes como `bombas.txt` que contienen múltiples mediciones y puntos (Ej. 1HM, 2V). El sistema filtrará inteligentemente para quedarse con la peor lectura de cada apoyo en el último mes.")
    
    with st.container(border=True):
        st.markdown("#### 📤 Carga de Datos")
        col_file_eq, col_btn_eq = st.columns([3, 1])
        with col_file_eq:
            file_eq = st.file_uploader("Selecciona el archivo de texto", type=['txt'], key="eq", label_visibility="collapsed")
        with col_btn_eq:
            if file_eq:
                st.button("⚙️ Analizar", key="btn_eq", use_container_width=True, on_click=lambda: None)
        
        if file_eq:
            st.divider()
            procesar_interfaz(file_eq, 'equipo')
