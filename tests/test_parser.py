import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.email_sync import extract_transaction_details_bci

def test_extract_bci_transaction_debit():
    email_body = """
    Monto $12.990
    Comercio LIDER SANTIAGO
    Fecha 10/04/2024
    Hora 13:20
    """

    result = extract_transaction_details_bci(email_body)

    assert result is not None
    assert result['Cost'] == 12990
    assert result['Merchant'] == 'LIDER SANTIAGO'
    assert result['Date'] == "10/04/2024 13:20"

