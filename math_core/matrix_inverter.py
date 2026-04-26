def invert_matrix(matrix: list[list[float]]) -> list[list[float]]:
    """Inverse une matrice carree via la methode de Gauss-Jordan."""
    n = len(matrix)

    # Creation de la matrice augmentee [A | I]
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]

    for i in range(n):
        pivot = aug[i][i]

        if pivot == 0.0:
            raise ValueError("Matrice singulière")

        for j in range(n * 2):
            aug[i][j] /= pivot

        for k in range(n):
            if k != i:
                factor = aug[k][i]
                for j in range(n * 2):
                    aug[k][j] -= factor * aug[i][j]

    # Extraction de la matrice inverse
    return [row[n:] for row in aug]
