import random
import numpy as np
from math import sqrt, floor, ceil
import cvxpy as cp
import value_at_risk
import smoother
#import weighter
import hyperparameters
import logging
from decorators import timed, profile
import torch

from multiprocessing import Pool as ThreadPool

class Nearest_neighbors_portfolio:


    def __init__(self, name, epsilon, __lambda, sanity=False, profile=False):
        self.name = name
        self.epsilon = epsilon
        self.__lambda = __lambda
        self.configure_logger()
        self.sanity=sanity
        self.profile=profile

    def __str__(self):
        return self.name

    def configure_logger(self):

        # create logger
        self.logger = logging.getLogger(self.name)
        self.logger.propagate = 0
        # set "verbosity"
        self.logger.setLevel(logging.INFO)
        # create console handler
        self.ch = logging.StreamHandler()
        self.ch.setLevel(logging.INFO)
        # create formatter and add it to the handlers
        # just a placeholder -- will be updated on the fly by timed decorator
        formatter = logging.Formatter('    %(name)s - %(levelname)s: - %(message)s')
        self.ch.setFormatter(formatter)
        # add the handlers to the logger
        self.logger.addHandler(self.ch)


    def set_num_samples(self, num_samples):

        self.num_samples = num_samples


    @timed
    def split_data(self):

        if self.sanity:

            # train on first num_samples_in_dataset samples; note: each row is a sample!
            self.X_tr = self.X_data[0:self.num_samples]
            self.Y_tr = self.Y_data[0:self.num_samples]

            # all the non-training data is considered "validation" data -- but this is actually out of sample data! almost all the data is out of sample
            self.X_val = self.X_data[self.num_samples:]
            self.Y_val = self.Y_data[self.num_samples:]

        else:
        
            # Training data
            train_perm = sorted(random.sample(range(len(self.X_data)), self.num_samples))
            self.X_tr = self.X_data[train_perm]
            self.Y_tr = self.Y_data[train_perm]

            # Validation data
            val_perm = sorted(list(set(range(len(self.X_data))) - set(train_perm)))
            self.X_val = self.X_data[val_perm]
            self.Y_val = self.Y_data[val_perm]

    @timed
    def compute_full_information_hyperparameters(self):

        self.hyperparameters_fi = self.compute_hyperparameters(self.Y_data, self.X_data)


    # can find analytically?
    @timed
    def compute_full_information_oos_cost(self):

        fi_learner_oos_cost = 0
        for x_val in self.X_data:
            c_fi, z_fi, b_fi, s_fi = self.optimize_nearest_neighbors_portfolio(self.Y_data, self.X_data,
                                                                               self.hyperparameters_fi, x_val)
            fi_learner_oos_cost += c_fi


        return fi_learner_oos_cost/len(self.X_data)
        

    @timed
    def compute_training_model_hyperparameters(self):

        # Find an appropriate nn smoother using training data:
        #     -- learn the distance function itself
        #         -- from which we get weighter based on heuristically chosen bandwidth and smoother)
        #     -- learn the number of nearest neighbours
        logging.info("Getting hyperparameters for training NN model...")
        self.hyperparameters_tr = self.compute_hyperparameters(self.Y_tr, self.X_tr)


    @timed
    def compute_training_model_oos_cost(self):

        tr_learner_oos_cost_true=0
        logging.info("Performing out of sample test for both (full information and training) NN models with " \
                     + str(len(self.X_val)) + " oos samples...")
        for idx, x_val in enumerate(self.X_val):

            if idx%10 == 0:
                logging.debug("out: " + str(idx))

            # oos cost of training learner (based on its knowledge of historical data) -- estimate
            c_tr, z_tr, b_tr, s_tr = self.optimize_nearest_neighbors_portfolio(self.Y_tr, self.X_tr,
                                                                               self.hyperparameters_tr, x_val)

            # find b (VaR) analytically
            b=value_at_risk.value_at_risk(x_val, z_tr, self.epsilon)

            # find true Y|X (returns Y distribution with weights)
            training_loss_fnc = lambda y: self.loss(z_tr, b, y)
            training_loss = np.apply_along_axis(training_loss_fnc, 1, self.Y_data)
            c_tr_true = self.compute_expected_response(training_loss, self.X_data, x_val, self.hyperparameters_fi)

            tr_learner_oos_cost_true += c_tr_true

        return tr_learner_oos_cost_true/len(self.X_val)


    @timed
    def load_data_from_csv(self, x_csv_filename, y_csv_filename):

        self.X_data = np.loadtxt(x_csv_filename, delimiter=",")
        self.Y_data = np.loadtxt(y_csv_filename, delimiter=",")


#    @timed
    def loss(self, z, b, y):
    
        return b + 1/self.epsilon*max(-np.dot(z, y)-b, 0)-self.__lambda*np.dot(z, y)

    '''
#    @timed
    def mahalanobis(self, x1, x2, A):
        \'''
        sqrt( (x1-x2)inv(A)(x1-x2) )
        \'''

        # Note: can get performance gain setting check_finite to false
        # Note2: what is returned is the lower left matrix despite lower=false, why?
        (A, lower) = cho_factor(A, overwrite_a=True, check_finite=True)

        # Distance function -- note that distance function -- smoother built on top
        return np.sqrt((x1-x2) @ cho_solve((A, lower), x1-x2, overwrite_b=True, check_finite=True))
    '''

    @timed
    def compute_hyperparameters(self, Y, X, p=0.2, smoother_list=[smoother.Smoother("Naive")]):

        # num rows X -- ie num samples
        num_samples_in_dataset = np.size(X, 0)

        # num cols X -- ie num covariates
        num_covariates = np.size(X, 1)

        # num cols of Y -- ie num assets
        num_assets = np.size(Y, 1)

        logging.debug("## Problem Parameters")
        logging.debug("1. Number of samples num_samples_in_dataset = " + str(num_samples_in_dataset))
        logging.debug("2. Label dimension : " + str(num_assets))
        logging.debug("3. Covariate dimension : " + str(num_assets))
        logging.debug("## Hyperparameter optimization")
        logging.debug("1. Proportion VALIDATION/TOTAL data =" + str(p))
        logging.debug("2. Considered Smoothers : " + str(smoother_list))

        # Compute covariance of covariates
        # TODO: check the math, why identity -- is this really mahalanobis?
        epsilonX = np.cov(X.T, bias=True) + np.identity(num_covariates)/num_samples_in_dataset

        upper_diag = torch.from_numpy(epsilonX)
        torch.potrf(upper_diag, out=upper_diag)

        # hyperparameters

        # TODO: add this unused julia code for NW portfolio?
        #D = [d(X[i, :], mean_X) for i in range(0,num_samples_in_dataset)]

        k_list = np.unique(np.round(np.linspace(max(1, floor(sqrt(num_samples_in_dataset)/1.5)), min(ceil(sqrt(num_samples_in_dataset)*1.5), num_samples_in_dataset), 20).astype('int')))

        # pick 20% of the original (training) samples as your validation set -- note: sorting not necessary
        if self.sanity:
            val = range(round(num_samples_in_dataset*p))
        else:
            val = sorted(random.sample(range(num_samples_in_dataset), round(num_samples_in_dataset*p)))

        # the remaining 80% is your new "training" set
        train = sorted(list(set(range(num_samples_in_dataset)) - set(val)))

        logging.debug("Number of k to test: " + str(len(k_list)))

        shortest_distance = -1
        for test_smoother in smoother_list:
            for test_k in k_list:

                # TODO: add this unused julia code for NW portfolio?
                #bandwidth_list = logspace(log10(minimum(D)), log10(maximum(D)), 10)
                if test_smoother == "Naive":
                    bandwidth_list = [1]

                for bandwidth in bandwidth_list:

                    test_hyperparameters = hyperparameters.Hyperparameters(test_k, test_smoother, upper_diag, bandwidth)

                    logging.debug("Smoother function : " + str(test_hyperparameters))
                    logging.debug("Number of neighbors : k = " + str(test_k))

                    
                    # find E[Y|xbar] for all X in validation set
                    expected_response = self.compute_expected_response(Y[train], X[train], X[val], test_hyperparameters)

                    # sum distance of all these E[Y|xbar] to true Y (respectively)
                    model_distance = np.sum((Y[val]-expected_response)**2)

                    # the shortest such distance corresponds to most accurate model, ie 
                    # this model has best hyperparameters, so we store them
                    if model_distance < shortest_distance or shortest_distance == -1:
                        shortest_distance = model_distance
                        shortest_distance_hyperparameters = test_hyperparameters

        return shortest_distance_hyperparameters


    @timed
    @profile
    def compute_expected_response(self, Y, X, Xbar, hyperparameters_object):

        """
        Arguments:

            Y: historical 'response' variable (typically asset returns)
            X: historical covariates
            Xbar: observation ("today's" covariate) -- can be interpreted as X context of interest
            hyperparameters_object: number of nearest neighbors k, smoother function, upper_diagonal, bandwidth
              -- uuper diagonal is Cholesky factor of mahalanobis matrix

        Returns:

            expected_response: expected response given Xbar observation/context


        Description:

            1. Find the k nearest neighbors of Xbar inside X, using mahalanobis distance
            2. Adjust k so that points just outside k-set which have equal distance as kth point are included
               -- call this adjusted k, "inclusive_k"
            3. Assign weights based on mahalanobis distance, a bandwidth, and a smoothing function
               -- Points further from xbar generally have smaller weights (naive smoother has equal weights)
               -- Smoother transforms distance to weights (eg for gaussian smoother, zero distance is center
                  of gaussian curve and further distances fall with distance from center)
               -- Higher bandwidth reduces distance as seen by smoothing function
                  -- depending on smoother this could have different effects (for example, for square uniform,
                     will give zero weight to fewer points)

        """
        num_samples_in_dataset = np.size(Y, 0)

        if Y.ndim == 2:
          num_assets = np.size(Y, 1)
        else: # Y.ndim ==1
          num_assets = 1

        num_covariates = np.size(X,1)

        if Xbar.ndim == 2:
            num_observations = np.size(Xbar, 0)
        else: # Xbar.dim == 1
            num_observations = 1

        Xbar_tensor = torch.from_numpy(Xbar).view(num_observations, -1)
        X_tensor = torch.from_numpy(X)
        Y_tensor = torch.from_numpy(Y)

        expected_response = np.zeros((num_observations, num_assets))

        logging.debug("# Hyper Parameters")
        logging.debug("1. Number of nearest neighbors k = " + str(hyperparameters_object.k))
        logging.debug("2. Smoother = " + str(hyperparameters_object))
        logging.debug("# Problem Parameters")
        logging.debug("1. Number of samples num_samples_in_dataset = " + str(num_samples_in_dataset))
        logging.debug("2. Label dimension : " + str(num_assets))
        logging.debug("3. Number of contexts : " + str(num_observations))

        for j in range(num_observations):

            ## Context of interest
            xbar = Xbar_tensor[j]

            # x1 - x2
            Xsub = X_tensor - xbar
            Z = torch.trtrs(Xsub.transpose(0,1), hyperparameters_object.upper_diag.transpose(0,1), upper=False)[
                0].transpose(0,1)

            # mahalanobis_distances: mahalanobis distance of each X vector to Xbar
            # L2 norm -- note: square root not necessary, since we only car about sorting not absolute actual number
            # but since speed of this call is not a bottleneck, this is fine
            mahalanobis_distances = torch.norm(Z, p=2, dim=1)

            ## SORT the data based on distance to xbar
            mahalanobis_distances_sorted, sorted_indices = torch.sort(mahalanobis_distances, 0)

            Y_sorted = Y_tensor[sorted_indices] # now local scope

            # adjust k to avoid eliminating equi-distant points
            inclusive_distance_boundary = mahalanobis_distances_sorted[hyperparameters_object.k - 1] + 1e-7

            # cast to int because of weird incompatibility between zero-dim tensor and int in pytorch 0.4.0
            inclusive_k = int(np.searchsorted(mahalanobis_distances_sorted, inclusive_distance_boundary,side='right'))

            # get indices of nearest neighbors
            inclusive_k_nearest_neighbor_indices = np.arange(inclusive_k)

            '''
            # This is a template for applying non-naive smoother to weigh nearest-neighbor points
            # The code was functionally tested and can be used as is except the for loop for which a form of 
            # broadcasting should be found, if possible for the smoother in question (for speed)           
            weights = mahalanobis_distances[inclusive_k_nearest_neighbor_indices] / hyperparameters_object.bandwidth
            for i in inclusive_k_nearest_neighbor_indices:
                weights[i] = hyperparameters_object.smoother(weights[i])
            weights = weights / sum(weights)
            
            # unsqueeze(1)/view(inclusive_k, 1) for broadcast multiplication to work as expected;
            # double() needed because smoother tested (naive) spits out a float (1.0) value instead of double.
            # double() likely won't be needed if/when this actually needs to be used
            # since smoother will likely divide/multiply an existing double() and therefore return a double
            weights = weights.view(inclusive_k, 1).double()

            # E[Y|xbar], ie weighted/"smoothed" average of the Y[i,:] corresponding to the nearest inclusive_k X
            expected_response[j] = torch.sum(weights * Y_tensor[inclusive_k_nearest_neighbor_indices].view(
                inclusive_k, num_assets), 0)
            
            '''
            expected_response[j] = torch.mean(Y_sorted[inclusive_k_nearest_neighbor_indices], 0)

        return expected_response
    
#    @timed
    def optimize_nearest_neighbors_portfolio(self, Y, X, hyperparameters_object, xbar):

        num_samples_in_dataset = np.size(Y, 0)

        num_assets = np.size(Y, 1)

        logging.debug("## Problem Parameters")
        logging.debug("1. Risk level CVaR epsilon = " + str(self.epsilon))
        logging.debug("2. Risk / Reward trade off __lambda = " + str(self.__lambda))
        logging.debug("## Problem dimensions ")
        logging.debug("1. Number of samples num_samples_in_dataset = " + str(num_samples_in_dataset))
        logging.debug("2. Label dimension : " + str(num_assets))
        logging.debug("## Hyper parameters")
        logging.debug("1. Number of nearest neighbors k = " + str(hyperparameters_object.k))
        logging.debug("2. Hyperparameters hyperparameters_object = " + str(hyperparameters_object))

        ## SORT the data based on distance to xbar        

        ## Context of interest
        # if I only input one observation, num_observations=1 and xbar = Xbar
        xbar_tensor = torch.from_numpy(xbar)

        X_tensor = torch.from_numpy(X)
        Y_tensor = torch.from_numpy(Y)

        # x1 - x2
        Xsub = X_tensor - xbar_tensor
        Z = torch.empty(X_tensor.size())
        for i in range(num_samples_in_dataset):
            # z = Linv * (x - xbar), where L is lower diagonal matrix
            Z[i] = torch.trtrs(Xsub[i], hyperparameters_object.upper_diag.transpose(0, 1), upper=False)[0].transpose(0, 1)

        # L2 norm -- note: square root not necessary, algorithm that doesn't take it could be faster
        # but since speed is not the objective here, this is fine
        # mahalanobis_distances = torch.norm(X_tensor)
        mahalanobis_distances = torch.norm(Z, p=2, dim=1)

        mahalanobis_distances, perm = torch.sort(mahalanobis_distances, 0)
        Y_nn = Y_tensor[perm].numpy() # now local scope

        # Define set of points of interest
        inclusive_distance_boundary = mahalanobis_distances[hyperparameters_object.k - 1] + 1e-7

        # adjusted k to avoid eliminating equi-distant points
        inclusive_k_nearest_neighbor_indices = torch.nonzero(mahalanobis_distances <= inclusive_distance_boundary).squeeze().numpy()

        # TODO: apply_along_axis is NOT fast -- use pytorch (perhaps with original loop) for speedup
        weight_from_xbar = lambda x1: hyperparameters_object.smoother(x1 / hyperparameters_object.bandwidth)
        # self.smoother(self.distance(x1, x2) / self.bandwidth)
        if inclusive_k_nearest_neighbor_indices.ndim > 0:
            S = np.empty_like(inclusive_k_nearest_neighbor_indices)
            for i in inclusive_k_nearest_neighbor_indices:
                S[i] = weight_from_xbar(mahalanobis_distances[i])
        # handle case where inclusive_k_nearest_neighbor_indices is scalar ("0-d array" technically)
        else:
            S = weight_from_xbar(mahalanobis_distances[inclusive_k_nearest_neighbor_indices])

        # Objective -- L is loss L(y,z) -- heavier weight to points closer to xbar
        # ie with greater distance ie higher hyperparameters_object
        # since this is a minimization not clear why need to divide by the sum of all distances?

        # OPTIMIZATION FORMULATION
        z = cp.Variable(num_assets)
        L = cp.Variable(len(inclusive_k_nearest_neighbor_indices))
        b = cp.Variable(1)

        # note: just "sum" instead of long sum_entries command appears to work
        obj = cp.Minimize(sum(cp.multiply(S,L))/sum(S))

        # Constraints
        # long only and unit leverage
        constrs = [z>=0, sum(z)==1]


        for i in inclusive_k_nearest_neighbor_indices:
            # this must define the loss function L. Second part obvious
            # not sure why first part is the same?
            constrs = constrs + [L[i] >= (1-1/self.epsilon)*b - (self.__lambda+1/self.epsilon)*sum(cp.multiply(Y_nn[i], z))]
            constrs = constrs + [L[i] >= b - self.__lambda*sum(cp.multiply(Y_nn[i, :], z))]

        # find optimal z, VaR (b) -- which minimizes total cost
        # this minimum total cost (over hitorical points) is problem.optval
        problem=cp.Problem(obj, constrs)

        # note: ECOS solver would probably be picked by cvxpy
        # TODO: run with default, see if it picks a faster one / compare speed of different solvers
        # note: more solvers can be added to core cvxpy
        # see "choosing a solver": http://www.cvxpy.org/tutorial/advanced/index.html 
        # note that SCS can use GPUs -- See https://github.com/cvxgrp/cvxpy/issues/245
        # can Boyd's POGS solver be used?
        # look into warm start -- make sure it is leveraged
        problem.solve(solver=cp.ECOS)

        return (problem.value, z.value, b.value, problem.status)
