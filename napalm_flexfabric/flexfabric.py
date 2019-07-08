# Copyright 2019 Steffen Walter. All rights reserved.
#
# The contents of this file are licensed under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with the
# License. You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.
"""
Napalm driver for HP FlexFabric devices
Read https://napalm.readthedocs.io for more information.
"""

from __future__ import print_function
from __future__ import unicode_literals

from netmiko import ConnectHandler, FileTransfer, InLineTransfer
from napalm.base.base import NetworkDriver
from napalm.base.exceptions import (
    CommandErrorException,
    ConnectionClosedException,
    ConnectionException,
)

from napalm.base.utils import py23_compat
import napalm.base.constants as C
import napalm.base.helpers
import re
import socket


# Constants
HOUR_SECONDS = 3600
DAY_SECONDS = 24 * HOUR_SECONDS
WEEK_SECONDS = 7 * DAY_SECONDS
YEAR_SECONDS = 365 * DAY_SECONDS

class FlexFabricDriver(NetworkDriver):
    """Napalm driver for HP FlexFabric"""

    def __init__(self, hostname, username, password, timeout=60, optional_args=None):
        self.device = None
        self.hostname = hostname
        self.username = username
        self.password = password
        self.timeout = timeout
        if optional_args is None:
            optional_args = {}


        # Netmiko possible arguments
        netmiko_argument_map = {
            'port': None,
            'secret': '',
            'verbose': False,
            'keepalive': 30,
            'global_delay_factor': 1,
            'use_keys': False,
            'key_file': None,
            'ssh_strict': False,
            'system_host_keys': False,
            'alt_host_keys': False,
            'alt_key_file': '',
            'ssh_config_file': None,
        }

        # Build dict of any optional Netmiko args
        self.netmiko_optional_args = {}
        for key, value in netmiko_argument_map.items():
            try:
                self.netmiko_optional_args[key] = optional_args[key]
            except KeyError:
                pass
        self.global_delay_factor = optional_args.get('global_delay_factor', 1)
        self.port = optional_args.get('port', 22)

        self.device = None
        self.config_replace = False
        self.interface_map = {}

        self.profile = ["flexfabric"]
    
    def open(self):
        """Open a connection to the device"""
        device_type = 'hp_comware_ssh'
        self.device = ConnectHandler(
            device_type=device_type,
            host=self.hostname,
            username=self.username,
            password=self.password,
            **self.netmiko_optional_args)
        # ensure in enable mode
        self.device.enable()
    
    def close(self):
        """Close the connection to the device."""
        self.device.disconnect()

    def _send_command(self, command):
        """Wrapper for self.device.send.command().
        If command is a list will iterate through commands until valid command.
        """
        try:
            if isinstance(command, list):
                for cmd in command:
                    output = self.device.send_command(cmd)
                    if "Invalid input: " not in output:
                        break
            else:
                output = self.device.send_command(command)
            return output
        except (socket.error, EOFError) as e:
            raise ConnectionClosedException(str(e))

    def is_alive(self):
        """ Returns a flag with the state of the connection."""
        if self.device is None:
            return {'is_alive': False}
        try:
            # SSH
            # Try sending ASCII null byte to maintain the connection alive
            null = chr(0)
            self.device.write_channel(null)
            return {
                'is_alive': self.device.remote_conn.transport.is_active()
            }
        except (socket.error, EOFError, OSError):
            # If unable to send, we can tell for sure that the connection is unusable
            return {'is_alive': False}

    def cli(self, commands):
        """
        Execute a list of commands and return the output in a dictionary format
        using the command as the key.
        """
        cli_output = dict()
        if type(commands) is not list:
            raise TypeError('Please enter a valid list of commands!')

        for command in commands:
            output = self._send_command(command)
            cli_output[py23_compat.text_type(command)] = output
        return cli_output

    def get_facts(self):
        """Return a set of facts from the devices."""
        # default values.
        vendor = "HP"
        uptime = -1
        serial_number, fqdn, os_version, hostname, domain_name, model = ("",) * 6

        # obtain output from device
        display_dev = self._send_command("display device manuinfo")
        display_ver = self._send_command("display version")
        display_curr_conf = self._send_command("display current-configuration | include sysname")
        display_domain = self._send_command("display domain | include Domain")
        display_interface = self._send_command("display interface brief | begin \"Interface            Link Speed\"")
        
        # serial number
        for line in display_dev.splitlines():
            if not line.startswith(" ") and "Chassis self" in line:
                chassis = True
            elif not line.startswith(" ") and not "Chassis self" in line:
                chassis = False
            if line.startswith(" DEVICE_SERIAL_NUMBER") and chassis:
                serial_number += (line.split(":")[1])
        serial_number = serial_number.strip()

        # uptime/model/version
        for line in display_ver.splitlines():
            if " uptime is " in line:
                model, uptime_str = line.split(" uptime is ")
                uptime = self.parse_uptime(uptime_str)
                model = model.strip()

            if "System image version" in line:
                os_version = line.split(":")[1].strip()
        
        # hostname
        hostname = display_curr_conf.split("sysname")[1].strip()
        
        # domain name
        domain_name = display_domain.split(":")[1].strip()

        #fqdn
        if domain_name != "system":
            fqdn = "{}.{}".format(hostname, domain_name)
        else:
            fqdn = hostname
        
        #interface list
        interface_list = []
        for line in display_interface.splitlines()[1:]:
            interface_list.append(line.split()[0])
        
        return {
            "uptime": int(uptime),
            "vendor": vendor,
            "os_version": py23_compat.text_type(os_version),
            "serial_number": py23_compat.text_type(serial_number),
            "model": py23_compat.text_type(model),
            "hostname": py23_compat.text_type(hostname),
            "fqdn": fqdn,
            "interface_list": interface_list,
        }

    def get_lldp_neighbors(self):
        #TODO
        return
    def get_lldp_neighbors_detail(self, interface=''):
        #TODO
        return
    def get_environment(self):
        #TODO
        return
    def get_config(self, retrieve='all'):
        #TODO
        return
    def get_ntp_servers(self):
        #TODO
        return
    def get_arp_table(self, vrf=""):
        #TODO
        return
    def get_mac_address_table(self):
        #TODO
        return
    def get_interfaces(self):
        #TODO
        return
    def get_interfaces_counters(self):
        #TODO
        return
    def _parse_interface_details(self, interface='all'):
        #TODO
        return

    @staticmethod
    def parse_uptime(uptime_str):
        """
        Extract the uptime string from the given Cisco IOS Device.
        Return the uptime in seconds as an integer
        """
        # Initialize to zero
        (years, weeks, days, hours, minutes, seconds) = (0, 0, 0, 0, 0, 0)

        uptime_str = uptime_str.strip()
        time_list = uptime_str.split(",")
        for element in time_list:
            if re.search("year", element):
                years = int(element.split()[0])
            elif re.search("week", element):
                weeks = int(element.split()[0])
            elif re.search("day", element):
                days = int(element.split()[0])
            elif re.search("hour", element):
                hours = int(element.split()[0])
            elif re.search("minute", element):
                minutes = int(element.split()[0])
            elif re.search("second", element):
                seconds = int(element.split()[0])

        uptime_sec = (
            (years * YEAR_SECONDS)
            + (weeks * WEEK_SECONDS)
            + (days * DAY_SECONDS)
            + (hours * 3600)
            + (minutes * 60)
            + seconds
        )
        return uptime_sec