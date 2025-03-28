"""Sklearn interface to the native nanoflann module"""
import copyreg
import warnings
from typing import Optional

import nanoflann_ext
import numpy as np
from sklearn.neighbors._base import KNeighborsMixin, NeighborsBase, RadiusNeighborsMixin
from sklearn.utils.validation import check_is_fitted
from scipy.sparse import csr_matrix, coo_matrix

SUPPORTED_TYPES = [np.float32, np.float64]
SUPPORTED_DIM = [2, 3]
SUPPORTED_METRIC = ["l1", "l2", "l11", "l22", "l21"]


def pickler(c):
    X = c._fit_X if hasattr(c, "_fit_X") else None
    return unpickler, (c.n_neighbors, c.radius, c.leaf_size, c.metric, X)


def unpickler(n_neighbors, radius, leaf_size, metric, X):
    # Recreate an kd-tree instance
    tree = KDTree(n_neighbors, radius, leaf_size, metric)
    # Unpickling of the fitted instance
    if X is not None:
        tree.fit(X)
    return tree


def _check_arg(points):
    if points.dtype not in SUPPORTED_TYPES:
        raise ValueError(f"Supported types: {points.dtype} not in {SUPPORTED_TYPES}")
    if len(points.shape) not in SUPPORTED_DIM:
        raise ValueError(f"Incorrect shape {len(points.shape)} not in {SUPPORTED_DIM}")


class KDTree(NeighborsBase, KNeighborsMixin, RadiusNeighborsMixin):
    def __init__(self, n_neighbors=5, radius=1.0, leaf_size=10, metric="l2"):

        metric = metric.lower()
        if metric not in SUPPORTED_METRIC:
            raise ValueError(f"Supported metrics: {SUPPORTED_METRIC}")

        super().__init__(
            n_neighbors=n_neighbors, radius=radius, leaf_size=leaf_size, metric=metric
        )

        self.index = None
        self._fit_X = None
        self._nb_vts_in_tree = None
        self._nb_vts_in_search = None

    def fit(self, X: np.ndarray, index_path: Optional[str] = None):
        """
        Args:
            X: np.ndarray data to use
            index_path: str Path to a previously built index. Allows you to not rebuild index.
                NOTE: Must use the same data on which the index was built.
        """
        _check_arg(X)
        if X.dtype == np.float32:
            self.index = nanoflann_ext.KDTree32(
                self.n_neighbors, self.leaf_size, self.metric, self.radius
            )
        else:
            self.index = nanoflann_ext.KDTree64(
                self.n_neighbors, self.leaf_size, self.metric, self.radius
            )

        if X.shape[1] > 64:
            warnings.warn(
                "KD Tree structure is not a good choice for high dimensional spaces."
                "Consider a more suitable search structure."
            )

        if self.metric == "l2" or self.metric == "l1":
            last_dim = 1
        else:
            if X.ndim == 3:
                last_dim = X.shape[2]
            else:
                raise ValueError(f"{self.metric} metric should be used with 3dim array")

        self._fit_X = X.reshape((X.shape[0], -1))
        self._nb_vts_in_tree = self._fit_X.shape[0]
        self.index.fit(self._fit_X, index_path if index_path is not None else "", last_dim)

    def get_data(self, copy: bool = True) -> np.ndarray:
        """Returns underlying data points. If copy is `False` then no modifications should be applied to the returned data.

        Args:
            copy: whether to make a copy.
        """
        check_is_fitted(self, ["_fit_X"], all_or_any=any)

        if copy:
            return self._fit_X.copy()
        else:
            return self._fit_X

    def save_index(self, path: str) -> int:
        """Save index to the binary file. NOTE: Data points are NOT stored."""
        return self.index.save_index(path)

    def radius_neighbors(self, X, radius=None, return_distance=True, n_jobs=1, no_return=False):
        check_is_fitted(self, ["_fit_X"], all_or_any=any)
        _check_arg(X)

        if X.ndim == 3:
            X = X.reshape((X.shape[0], -1))

        if radius is None:
            radius = self.radius

        if n_jobs == 1:
            if return_distance:
                self.index.radius_neighbors_idx_dists(X, radius)
            else:
                self.index.radius_neighbors_idx(X, radius)
        else:
            if return_distance:
                self.index.radius_neighbors_idx_dists_multithreaded(X, radius, n_jobs)
            else:
                self.index.radius_neighbors_idx_multithreaded(X, radius, n_jobs)

        self._nb_vts_in_search = X.shape[0]

        if no_return:
            return

        if return_distance:
            return self.index.getResultIndicesRow(), self.index.getResultIndicesCol(), self.index.getResultDists()

        return self.index.getResultIndicesRow(), self.index.getResultIndicesCol()

    # Results getter with sparse matrices
    def get_dists(self):
        return self.index.getResultDists()

    def get_rows(self):
        return self.index.getResultIndicesRow()

    def get_cols(self):
        return self.index.getResultIndicesCol()

    def get_csr_matrix(self):
        mtx_shape = None
        if self._nb_vts_in_tree and self._nb_vts_in_search:
            mtx_shape = (self._nb_vts_in_tree, self._nb_vts_in_search)
        return csr_matrix((self.get_dists(), self.get_cols(), self.index.getResultIndicesPtr()), shape=mtx_shape)

    def get_coo_matrix(self):
        mtx_shape = None
        if self._nb_vts_in_tree and self._nb_vts_in_search:
            mtx_shape = (self._nb_vts_in_tree, self._nb_vts_in_search)
        return coo_matrix((self.get_dists(), (self.get_rows(), self.get_cols())), shape=mtx_shape)

    def get_csc_matrix(self):
        return self.get_coo_matrix().to_csc()

    # Advanced operation, using mean-points and full-points array
    def radius_neighbors_full(self, X_mpts, Data_full, X_full, radius, n_jobs=1):
        if X_mpts.ndim == 3:
            X_mpts = X_mpts.reshape((X_mpts.shape[0], -1))
        if Data_full.ndim == 3:
            Data_full = Data_full.reshape((Data_full.shape[0], -1))
        if X_full.ndim == 3:
            X_full = X_full.reshape((X_full.shape[0], -1))

        nb_mpts = X_mpts.shape[1]
        nb_dim = X_full.shape[1]

        assert(X_mpts.shape[1] <= X_full.shape[1])

        assert(X_full.shape[1] == Data_full.shape[1])
        assert(X_mpts.shape[0] == X_full.shape[0])
        assert(self.get_data(copy=False).shape[0] == Data_full.shape[0])
        assert(nb_dim % nb_mpts == 0)

        mpts_radius = radius * nb_mpts / nb_dim

        if n_jobs == 1:
            self.index.radius_neighbors_idx_dists_full(X_mpts, Data_full, X_full, mpts_radius, radius)
        else:
            self.index.radius_neighbors_idx_dists_full_multithreaded(X_mpts, Data_full, X_full, mpts_radius, radius, n_jobs)

    def fit_and_radius_search(self, tree_vts, search_vts, radius, n_jobs=1, nb_mpts=None):
        assert(np.alltrue(tree_vts.shape[1:] == search_vts.shape[1:]))

        if nb_mpts:
            if not(self.metric in ["l1", "l2", "l11", "l21"]):
                raise ValueError(f"Only  l1, l2, l11, or l21  can be used with nb_mpts")

            if tree_vts.shape[1] % nb_mpts != 0:
                raise ValueError(f"nb_mpts must be a divisor of tree_vts.shape[2]")

            nb_averaged = tree_vts.shape[1] // nb_mpts
            tree_mpts = np.mean(tree_vts.reshape((tree_vts.shape[0], nb_mpts, nb_averaged, -1)), axis=2)
            search_mpts = np.mean(search_vts.reshape((search_vts.shape[0], nb_mpts, nb_averaged, -1)), axis=2)

            self.fit(tree_mpts)
            self.radius_neighbors_full(search_mpts, tree_vts, search_vts, radius, n_jobs=n_jobs)

        else:
            self.fit(tree_vts)
            self.radius_neighbors(search_vts, radius=radius, n_jobs=n_jobs,
                                  return_distance=True, no_return=True)


# Register pickling of non-trivial types
copyreg.pickle(KDTree, pickler, unpickler)
