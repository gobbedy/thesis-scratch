#!/usr/bin/env python3.6
import logging
import portfolio_simulator

# note: levels are:
# CRITICAL
# ERROR
# WARNING
# INFO
# DEBUG
# NOTSET

# the level is the threshold,
# eg if INFO is set, all info and "worse" (warning, error, critical") messages will be printed
# eg if ERROR is set, only ERROR and CRITICAL messages will be printed
# note: use logging cookbook for more granularity: https://docs.python.org/2/howto/logging-cookbook.html

# parse arguments
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("-d", "--sanity", help="make code deterministic and use small existing dataset for debugging", action="store_true")
parser.add_argument("-p", "--profile", help="turn on advanced profiling", action="store_true")
args = parser.parse_args()

# configure logger
logger = logging.getLogger('portfolio_simulation')
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter('%(name)s - %(levelname)s: - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

# launch simulation
logger.info("Start portfolio simulation")
num_iterations=1
num_samples_list=[8]
simulator = portfolio_simulator.Portfolio_simulator("simulator", num_iterations, num_samples_list, args.sanity, args.profile, "data/X_nt.csv.short", "data/Y_nt.csv.short")
simulator.run_simulation()
logger.info("End portfolio simulation")

