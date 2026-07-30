from src.ingestion.chunker import chunk_file


def test_function_becomes_its_own_chunk():
    code = "def add(a, b):\n    return a + b\n"
    chunks = chunk_file("app.py", code)
    function_chunks = [c for c in chunks if c.chunk_type == "function"]
    assert len(function_chunks) == 1
    assert function_chunks[0].name == "add"
    assert "return a + b" in function_chunks[0].content


def test_class_becomes_its_own_chunk():
    code = "class Widget:\n    def __init__(self):\n        pass\n"
    chunks = chunk_file("app.py", code)
    class_chunks = [c for c in chunks if c.chunk_type == "class"]
    assert len(class_chunks) == 1
    assert class_chunks[0].name == "Widget"


def test_multiple_functions_produce_separate_chunks():
    code = "def a():\n    pass\n\n\ndef b():\n    pass\n"
    chunks = chunk_file("app.py", code)
    names = {c.name for c in chunks if c.chunk_type == "function"}
    assert names == {"a", "b"}


def test_module_level_code_not_covered_by_a_function_becomes_its_own_chunk():
    code = "import os\n\ndef f():\n    pass\n"
    chunks = chunk_file("app.py", code)
    module_chunks = [c for c in chunks if c.chunk_type == "module"]
    assert any("import os" in c.content for c in module_chunks)


def test_chunks_are_sorted_by_start_line():
    code = "x = 1\n\ndef f():\n    pass\n"
    chunks = chunk_file("app.py", code)
    starts = [c.start_line for c in chunks]
    assert starts == sorted(starts)


def test_syntax_error_falls_back_to_window_chunking():
    code = "def broken(:\n    pass\n"
    chunks = chunk_file("app.py", code)
    assert len(chunks) >= 1
    assert all(c.chunk_type == "window" for c in chunks)


def test_non_python_file_uses_window_chunking():
    code = "\n".join(f"line {i}" for i in range(100))
    chunks = chunk_file("notes.txt", code)
    assert all(c.chunk_type == "window" for c in chunks)
    assert len(chunks) > 1  # 100 lines with a 60-line window should split


def test_empty_content_produces_no_chunks():
    assert chunk_file("app.py", "") == []
    assert chunk_file("app.py", "   \n  ") == []


def test_each_chunk_carries_the_filename():
    code = "def f():\n    pass\n"
    chunks = chunk_file("src/app.py", code)
    assert all(c.filename == "src/app.py" for c in chunks)
