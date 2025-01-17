import pandas as pd

# Define the mapping of Rubro to Simplified Categories
category_mapping = {
    "ACTIVIDADES DE ATENCION DE LA SALUD HUMANA Y DE ASISTENCIA SOCIAL": "Salud y Asistencia Social",
    "TRANSPORTE Y ALMACENAMIENTO": "Transporte y Logística",
    "INDUSTRIA MANUFACTURERA": "Manufactura",
    "ACTIVIDADES DE ALOJAMIENTO Y DE SERVICIO DE COMIDAS": "Alojamiento y Comidas",
    "OTRAS ACTIVIDADES DE SERVICIOS": "Servicios Personales",
    "COMERCIO AL POR MAYOR Y AL POR MENOR; REPARACION DE VEHICULOS AUTOMOTORES Y MOTOCICLETAS": "Comercio y Reparaciones",
    "ACTIVIDADES INMOBILIARIAS": "Bienes Raíces",
    "Valor por Defecto": "Sin Clasificar",
    "ACTIVIDADES PROFESIONALES, CIENTIFICAS Y TECNICAS": "Servicios Profesionales y Técnicos",
    "ACTIVIDADES DE SERVICIOS ADMINISTRATIVOS Y DE APOYO": "Servicios Administrativos y Apoyo",
    "AGRICULTURA, GANADERIA, SILVICULTURA Y PESCA": "Agricultura y Pesca",
    "INFORMACION Y COMUNICACIONES": "Tecnología y Comunicaciones",
    "ENSEÑANZA": "Educación",
    "ACTIVIDADES FINANCIERAS Y DE SEGUROS": "Finanzas y Seguros",
    "ACTIVIDADES ARTISTICAS, DE ENTRETENIMIENTO Y RECREATIVAS": "Entretenimiento y Cultura",
    "CONSTRUCCION": "Construcción",
    "SUMINISTRO DE ELECTRICIDAD, GAS, VAPOR Y AIRE ACONDICIONADO": "Servicios Públicos (Luz, Gas, etc.)",
    "EXPLOTACION DE MINAS Y CANTERAS": "Minería y Canteras",
    "SUMINISTRO DE AGUA; EVACUACION DE AGUAS RESIDUALES, GESTION DE DESECHOS Y DESCONTAMINACION": "Agua y Desechos",
    "ADMINISTRACION PUBLICA Y DEFENSA; PLANES DE SEGURIDAD SOCIAL DE AFILIACION OBLIGATORIA": "Gobierno y Defensa",
    "ACTIVIDADES DE ORGANIZACIONES Y ORGANOS EXTRATERRITORIALES": "Organizaciones Internacionales",
    "ACTIVIDADES DE LOS HOGARES COMO EMPLEADORES; ACTIVIDADES NO DIFERENCIADAS DE LOS HOGARES": "Hogar y Empleadores"
}

# Load the original TXT file
file_path = "C:/Users/simon/Documents/FinanceApp/merchants.txt"
output_path = "C:/Users/simon/Documents/FinanceApp/merchants_with_categories.txt"

df = pd.read_csv(file_path, delimiter='\t', encoding='utf-8')

# Add a "Category" column based on the Rubro
df['Category'] = df['Rubro económico'].map(category_mapping)

# Print the first few rows to verify
print("Preview of the updated file:")
print(df[['Razón social', 'Rubro económico', 'Category']].head())

# Save the updated file
df.to_csv(output_path, sep='\t', index=False, encoding='utf-8')

print(f"Updated file saved to {output_path}")
