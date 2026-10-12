from backend.tools import clean_code, extract_sql, is_valid_code


def test_extract_sql_strips_fences_and_semicolon():
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"


def test_extract_sql_prepends_select_when_missing():
    assert extract_sql("COUNT(*) FROM orders") == "SELECT COUNT(*) FROM orders"


def test_clean_code_removes_markdown_fence():
    raw = "```python\nimport matplotlib.pyplot as plt\nplt.plot([1])\n```"
    assert clean_code(raw) == "import matplotlib.pyplot as plt\nplt.plot([1])"


def test_clean_code_removes_ansi_codes():
    assert clean_code("\x1b[31mplt.plot([1])\x1b[0m") == "plt.plot([1])"


def test_is_valid_code():
    good = "import matplotlib.pyplot as plt\nplt.figure(figsize=(10, 6))\nplt.tight_layout()"
    assert is_valid_code(good)
    assert not is_valid_code("")
    assert not is_valid_code("x = 1 + 2 + 3 + 4 + 5 + 6 + 7 + 8")        # không có plt/sns
    assert not is_valid_code("plt.plot([1, 2, 3]\nplt.tight_layout()")    # SyntaxError
