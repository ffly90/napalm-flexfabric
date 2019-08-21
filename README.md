# napalm-flexfabric

Functions implemented
=====================

Beside the functions to handle the connection to the switch, the following functions are implemented:

 * get_facts()
 * get_environment()
 * get_lldp_neighbors()
 * get_interfaces()
 * get_config()

They have been tested on the following hardware with the corresponding firmware that was up to date 2019-07:
 * flexfabric 5824
 * flexfabric 5940
 * flexfabric 7910


Documentation
=============
There are generic docstrings for all the NAPALM functions in the projects repository:
https://github.com/napalm-automation/napalm/blob/develop/napalm/base/base.py



Installation
============

To install the driver, clone the repository and execute the following command:
```
$ pip install git+https://github.com/firefly-serenity/napalm-flexfabric.git
```

License
=======

ASL2.0
