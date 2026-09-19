from wax.memory.embeddings import cosine_similarity


def test_cosine_identical():
    v = [0.1, 0.2, 0.3]
    assert cosine_similarity(v, v) > 0.99


def test_cosine_orthogonal():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) < 0.01


def test_cosine_empty():
    assert cosine_similarity([], [1.0]) == 0.0
