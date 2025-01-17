import pandas as pd

def print_unique_rubros(file_path):
    # Read the TXT file
    df = pd.read_csv(file_path, delimiter='\t', encoding='utf-8')

    # Extract unique "Rubro económico"
    unique_rubros = df["Rubro económico"].drop_duplicates().reset_index(drop=True)

    # Print the unique rubros
    print("Unique Rubros:")
    for idx, rubro in enumerate(unique_rubros, start=1):
        print(f"{idx}. {rubro}")

# Call the function
print_unique_rubros(r"C:\Users\simon\Documents\FinanceApp\merchants.txt")
