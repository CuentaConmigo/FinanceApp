import pandas as pd

def analyze_txt_file(file_path):
    # Read the TXT file as a tab-delimited file
    df = pd.read_csv(file_path, delimiter='\t', encoding='utf-8')
    
    # Show the first few rows of the DataFrame
    print("Preview of the data:")
    print(df.head())

    # Analyze "Rubro económico" (categories)
    print("\nUnique categories ('Rubro económico'):")
    print(df['Rubro económico'].value_counts())

    # Analyze "Subrubro económico" (subcategories)
    print("\nUnique subcategories ('Subrubro económico'):")
    print(df['Subrubro económico'].value_counts())

    # Count the unique rubros and subrubros
    unique_categories = df['Rubro económico'].nunique()
    unique_subcategories = df['Subrubro económico'].nunique()
    print(f"\nNumber of unique categories: {unique_categories}")
    print(f"Number of unique subcategories: {unique_subcategories}")

# Call the function with the path to your TXT file
analyze_txt_file(r"C:\Users\simon\Documents\FinanceApp\cleaned_merchants_with_categories.txt")
