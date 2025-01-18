import pandas as pd

# Load the merchants_with_categories.txt file
file_path = "merchants_with_categories.txt"
columns_to_keep = ['Razón social','Rubro económico','Subrubro económico' ,'Category']  # Keep only these columns

data = pd.read_csv(file_path, delimiter='\t', encoding='utf-8')
cleaned_data = data[columns_to_keep]

# Save the cleaned version
cleaned_file_path = "cleaned_merchants_with_categories.txt"
cleaned_data.to_csv(cleaned_file_path, sep='\t', index=False)
print(f"Cleaned file saved to {cleaned_file_path}")

# Repeat for merchants.txt if needed
file_path = "merchants.txt"
columns_to_keep = ['Razón social']  # Example: keep only the merchant name
data = pd.read_csv(file_path, delimiter='\t', encoding='utf-8')
cleaned_data = data[columns_to_keep]

cleaned_file_path = "cleaned_merchants.txt"
cleaned_data.to_csv(cleaned_file_path, sep='\t', index=False)
print(f"Cleaned file saved to {cleaned_file_path}")
