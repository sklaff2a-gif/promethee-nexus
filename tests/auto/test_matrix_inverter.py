import pytest
from math_core.matrix_inverter import invert_matrix


def test_invert_singular_matrix_raises_error():
    # Une matrice avec un 0 sur la diagonale forcera un pivot nul a l'iteration 0
    singular_matrix = [[0.0, 1.0], [1.0, 0.0]]

    with pytest.raises(ValueError, match="singulière"):
        invert_matrix(singular_matrix)
