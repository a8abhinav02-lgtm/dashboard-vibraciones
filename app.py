# # Procesador de Reportes de Vibraciones (Con Dashboard y Gráficos)
# %%
!pip install xlsxwriter
import pandas as pd
import os
import xlsxwriter
from google.colab import files

# ## Funciones Auxiliares
def calcular_criticidad(diferencia, unidad):
    """Evalúa la criticidad con umbrales independientes para Velocidad y Aceleración."""
    if diferencia == '---' or pd.isnull(diferencia):
        return '---'
    
    try:
        val = float(diferencia)
        if unidad == 'mm/Sec RMS':
            if val >= 1.0: return 'ALTO'
            elif 0.25 <= val < 1.0: return 'MEDIA'
            elif -0.5 <= val < 0.25: return 'NORMAL'
            else: return 'REVISAR'
            
        elif unidad == 'G-s RMS':
            if val >= 0.25: return 'ALTO'
            elif 0.10 <= val < 0.25: return 'MEDIA'
            elif -0.20 <= val < 0.10: return 'NORMAL'
            else: return 'REVISAR'
            
    except ValueError:
        return '---'

def procesar_unidad(df_in, month_order, latest_month, group_keys, unidad):
    """Encapsula la lógica de transformación y estadísticas."""
    if df_in.empty: return None
        
    pivot_table = df_in.pivot_table(values='Value', index=group_keys, columns='Month', aggfunc='first').reset_index()

    for month in month_order:
        if month not in pivot_table.columns:
            pivot_table[month] = '---'

    pivot_table = pivot_table[group_keys + month_order]
    pivot_table = pivot_table.astype(object).fillna('---')

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
    
    stats['Latest vs Median'] = stats.apply(
        lambda x: round(x['Latest Value'] - x['Previous Median'], 3) if pd.notnull(x['Latest Value']) and x['Previous Median'] != '---' else '---', axis=1
    )
    stats['Latest vs Avg'] = stats.apply(
        lambda x: round(x['Latest Value'] - x['Previous Avg'], 3) if pd.notnull(x['Latest Value']) and x['Previous Avg'] != '---' else '---', axis=1
    )
    
    stats['Criticality'] = stats['Latest vs Avg'].apply(lambda x: calcular_criticidad(x, unidad))
    
    cols_stats = [f'Avg {unidad}', f'Std {unidad}', 'Latest vs Avg', 'Latest vs Median', 'Criticality']
    stats_final = stats[group_keys + cols_stats]
    
    pivot_table_final = pivot_table.merge(stats_final, on=group_keys, how='left')
    final_columns = group_keys + cols_stats + month_order
    pivot_table_final = pivot_table_final[final_columns]
    
    cols_to_format = month_order + [f'Avg {unidad}', f'Std {unidad}', 'Latest vs Avg', 'Latest vs Median']
    for col in cols_to_format:
        if col in pivot_table_final.columns:
            pivot_table_final[col] = pivot_table_final[col].apply(
                lambda x: f"{float(x):.3f}" if isinstance(x, (int, float)) and pd.notnull(x) else x
            )
            
    return pivot_table_final

# ## 1. Subida y Selección de Archivo
print("Por favor, selecciona tu archivo .txt exportado del software de vibraciones:")
uploaded = files.upload()

if not uploaded:
    raise FileNotFoundError("No se seleccionó ningún archivo. Por favor, vuelve a ejecutar la celda.")

file_name = list(uploaded.keys())[0]
file_path = file_name
base_name = os.path.splitext(file_path)[0]

print(f"\nProcesando archivo: {file_path}...")

# ## 2. Lectura y Extracción de Datos
with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

data = []
current_area = None
current_equipment = None
current_unit = None
meses_validos = ['Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov']

for row_str in lines:
    row_str = row_str.strip()
    if not row_str: continue

    if row_str.startswith('Area:'):
        current_area = row_str.split('Area:')[1].strip()
        continue
    
    # Modificación aquí para incluir 'PP', 'PF', 'PC', 'PR'
    if any(row_str.startswith(prefix) for prefix in ['PP', 'PF', 'PC', 'PR']) and not row_str.startswith('Database:') and not row_str.startswith('Report Date:') and not row_str.startswith('Reporte de Vibraciones - Resumen de Rutas'):
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
            value = parts[2] if len(parts) > 2 else '-------'
            
            if value != '-------':
                try: value = float(value)
                except ValueError: value = None
            else: value = None
                
            data.append({
                'Area': current_area, 'Equipment': current_equipment,
                'Unit': current_unit, 'Month': f"{month} {year}", 'Value': value
            })

# ## 3. Procesamiento en DataFrames
df_data = pd.DataFrame(data)

if df_data.empty: raise ValueError("No se encontraron datos válidos de vibración en el archivo.")

month_order = sorted(df_data['Month'].unique(), key=lambda x: pd.to_datetime(x, format='%b %Y'))
latest_month = month_order[-1] if month_order else None
print(f"Última fecha de medición detectada: {latest_month}")

group_keys = ['Area', 'Equipment', 'Unit']
df_vel = df_data[df_data['Unit'] == 'mm/Sec RMS']
df_acc = df_data[df_data['Unit'] == 'G-s RMS']

tabla_vel = procesar_unidad(df_vel, month_order, latest_month, group_keys, 'mm/Sec RMS')
tabla_acc = procesar_unidad(df_acc, month_order, latest_month, group_keys, 'G-s RMS')

# ## 4. Exportación a Excel con Dashboard
output_xlsx = base_name + '_vibration_report.xlsx'

with pd.ExcelWriter(output_xlsx, engine='xlsxwriter') as writer:
    workbook = writer.book
    
    formato_alto = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006'})
    formato_media = workbook.add_format({'bg_color': '#FFEB9C', 'font_color': '#9C6500'})
    formato_revisar = workbook.add_format({'bg_color': '#E0E0E0', 'font_color': '#000000'})
    
    # ------------------ CREACIÓN DEL DASHBOARD ------------------
    ws_dash = workbook.add_worksheet('Dashboard')
    ws_dash.set_column('A:B', 20)
    ws_dash.set_column('C:E', 25)
    
    fmt_title = workbook.add_format({'bold': True, 'font_size': 14, 'bg_color': '#1F497D', 'font_color': 'white', 'align': 'center'})
    fmt_header = workbook.add_format({'bold': True, 'bg_color': '#DCE6F1', 'border': 1})
    fmt_border = workbook.add_format({'border': 1})
    
    ws_dash.merge_range('A1:G1', 'DASHBOARD EJECUTIVO - ESTADO DE VIBRACIONES', fmt_title)
    
    current_row = 2
    
    def crear_bloque_kpi(df, titulo, fila_inicio):
        if df is None or df.empty: return fila_inicio
        
        total = len(df)
        altos = len(df[df['Criticality'] == 'ALTO'])
        medias = len(df[df['Criticality'] == 'MEDIA'])
        normales = len(df[df['Criticality'] == 'NORMAL'])
        revisar = len(df[df['Criticality'] == 'REVISAR'])
        alertas = altos + medias
        
        ws_dash.write(fila_inicio, 0, f"KPIs: {titulo}", fmt_header)
        ws_dash.write(fila_inicio+1, 0, 'Estado', fmt_header)
        ws_dash.write(fila_inicio+1, 1, 'Equipos', fmt_header)
        
        datos = [('ALTO', altos, formato_alto), ('MEDIA', medias, formato_media), 
                 ('NORMAL', normales, fmt_border), ('REVISAR', revisar, formato_revisar)]
        
        for i, (estado, cant, fmt) in enumerate(datos):
            ws_dash.write(fila_inicio+2+i, 0, estado, fmt)
            ws_dash.write(fila_inicio+2+i, 1, cant, fmt_border)
            
        ws_dash.write(fila_inicio+7, 0, 'Total Evaluados', fmt_header)
        ws_dash.write(fila_inicio+7, 1, total, fmt_header)
        ws_dash.write(fila_inicio+8, 0, 'Equipos en Alerta', fmt_header)
        ws_dash.write(fila_inicio+8, 1, alertas, formato_alto)
        
        # Insertar Gráfico
        chart = workbook.add_chart({'type': 'pie'})
        chart.add_series({
            'name': f'Salud {titulo}',
            'categories': ['Dashboard', fila_inicio+2, 0, fila_inicio+5, 0],
            'values':     ['Dashboard', fila_inicio+2, 1, fila_inicio+5, 1],
            'points': [{'fill': {'color': '#FF0000'}}, {'fill': {'color': '#FFC000'}},
                       {'fill': {'color': '#92D050'}}, {'fill': {'color': '#A6A6A6'}}],
        })
        chart.set_title({'name': f'Distribución {titulo}'})
        ws_dash.insert_chart(fila_inicio, 3, chart, {'x_scale': 0.8, 'y_scale': 0.8})
        
        return fila_inicio + 15

    current_row = crear_bloque_kpi(tabla_vel, 'Velocidad', current_row)
    current_row = crear_bloque_kpi(tabla_acc, 'Aceleración', current_row)
    
    # ------------------ LISTA NEGRA ------------------
    current_row += 1
    ws_dash.merge_range(f'A{current_row}:G{current_row}', 'LISTA NEGRA - PRIORIDADES DE MANTENIMIENTO (ALTO Y MEDIA)', fmt_title)
    current_row += 2
    
    def escribir_lista_negra(df, fila):
        if df is None or df.empty: return fila
        df_alertas = df[df['Criticality'].isin(['ALTO', 'MEDIA'])].copy()
        if df_alertas.empty: return fila
        
        df_alertas.sort_values(by='Criticality', inplace=True) # Ordena ALTO primero
        
        encabezados = ['Area', 'Equipment', 'Unit', 'Criticality', 'Latest vs Avg', 'Latest vs Median']
        for col_num, col_name in enumerate(encabezados):
            ws_dash.write(fila, col_num, col_name, fmt_header)
        fila += 1
        
        for _, row in df_alertas.iterrows():
            ws_dash.write(fila, 0, row['Area'], fmt_border)
            ws_dash.write(fila, 1, row['Equipment'], fmt_border)
            ws_dash.write(fila, 2, row['Unit'], fmt_border)
            fmt_c = formato_alto if row['Criticality'] == 'ALTO' else formato_media
            ws_dash.write(fila, 3, row['Criticality'], fmt_c)
            ws_dash.write(fila, 4, row['Latest vs Avg'], fmt_border)
            ws_dash.write(fila, 5, row['Latest vs Median'], fmt_border)
            fila += 1
        return fila + 2

    current_row = escribir_lista_negra(tabla_vel, current_row)
    current_row = escribir_lista_negra(tabla_acc, current_row)
    # ------------------------------------------------------------

    def aplicar_formatos_a_hoja(df_tabla, nombre_hoja):
        if df_tabla is None or df_tabla.empty: return
        df_tabla.to_excel(writer, sheet_name=nombre_hoja, index=False)
        worksheet = writer.sheets[nombre_hoja]
        (max_row, max_col) = df_tabla.shape
        worksheet.add_table(0, 0, max_row, max_col - 1, {
            'columns': [{'header': col} for col in df_tabla.columns],
            'autofilter': True, 'style': 'Table Style Medium 9'
        })
        if 'Criticality' in df_tabla.columns:
            col_crit = df_tabla.columns.get_loc('Criticality')
            rango = xlsxwriter.utility.xl_range(1, col_crit, max_row, col_crit)
            worksheet.conditional_format(rango, {'type': 'cell', 'criteria': '==', 'value': '"ALTO"', 'format': formato_alto})
            worksheet.conditional_format(rango, {'type': 'cell', 'criteria': '==', 'value': '"MEDIA"', 'format': formato_media})
            worksheet.conditional_format(rango, {'type': 'cell', 'criteria': '==', 'value': '"REVISAR"', 'format': formato_revisar})

    aplicar_formatos_a_hoja(tabla_vel, 'Velocidad')
    aplicar_formatos_a_hoja(tabla_acc, 'Aceleracion')

print(f"\n¡Proceso finalizado! Se ha generado tu reporte con Dashboard Ejecutivo:\n📥 {output_xlsx}")
