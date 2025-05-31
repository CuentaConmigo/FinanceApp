import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

def analyze_cleaned_merchants(file_path):
    # Load the file
    df = pd.read_csv(file_path, delimiter='\t', encoding='utf-8')

    print("📄 Columns detected:", list(df.columns))

    # Show the first few rows
    print("\n🔍 Preview of the data:")
    print(df.head())

    # Focus on cleaned 'Category' column
    if 'Category' not in df.columns:
        raise Exception("⚠️ Expected a 'Category' column for clean labels. Please check the file.")

    print("\n📦 Top categories (cleaned):")
    cat_counts = df['Category'].value_counts()
    print(cat_counts)

    print(f"\n🔢 Unique categories: {df['Category'].nunique()}")

    # Bar plot of top categories
    plt.figure(figsize=(10, 6))
    sns.barplot(x=cat_counts.values[:15], y=cat_counts.index[:15], palette="coolwarm")
    plt.title("Top 15 Categories by Merchant Count")
    plt.xlabel("Number of Merchants")
    plt.ylabel("Clean Category")
    plt.tight_layout()
    plt.show()

    # Example merchants per top 5 categories
    print("\n📋 Example merchants in top 5 categories:\n")
    top5 = cat_counts.head(5).index.tolist()
    for cat in top5:
        sample = df[df['Category'] == cat]['Razón social'].dropna().unique()[:5]
        print(f"▶️ {cat}: {', '.join(sample)}")

    # Check overlap between Category and Rubro económico if both exist
    if 'Rubro económico' in df.columns:
        print("\n🔁 Mapping from Rubro económico to Category:")
        crossmap = df.groupby(['Rubro económico', 'Category']).size().unstack(fill_value=0)
        print(crossmap.head(10))

    # Null values
    print("\n🧼 Null value summary:")
    print(df.isnull().sum())

# Run the function
analyze_cleaned_merchants(r"C:\Users\simon\Documents\FinanceApp\cleaned_merchants_with_categories.txt")
