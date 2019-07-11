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
import string


# Constants
HOUR_SECONDS = 3600
DAY_SECONDS = 24 * HOUR_SECONDS
WEEK_SECONDS = 7 * DAY_SECONDS
YEAR_SECONDS = 365 * DAY_SECONDS

class FlexFabricDriver(NetworkDriver):
    """Napalm driver for HPE FlexFabric Switches"""

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
        display_interface = self._send_command("display interface brief")

        # serial number
        chassis = False
        for line in display_dev.splitlines():
            if not line.startswith(" ") and ("Chassis self" in line or "Slot" in line)\
            or ("Slot" in line and "CPU" in line):
                chassis = True
            if chassis and "DEVICE_SERIAL_NUMBER" in line:
                serial_number += (line.split(":")[1])
                chassis = False
        serial_number = serial_number.strip()

        # uptime/model/os_version
        for line in display_ver.splitlines():
            if " uptime is " in line:
                model, uptime_str = line.split(" uptime is ")
                uptime = self.parse_uptime(uptime_str)
                model = model.strip()

            if "System image version" in line:
                os_version = line.split(":")[1].strip()
            elif "Comware Software, Version" in line:
                os_version = line.lstrip("Comware Software, Version")

        # hostname
        hostname = display_curr_conf.split("sysname")[1].strip()

        # domain name
        domain_name = display_domain.splitlines()[0].split(":")[1].strip()

        #fqdn
        if domain_name != "system":
            fqdn = "{}.{}".format(hostname, domain_name)
        else:
            fqdn = hostname

        #interface list
        interface_list = []
        active = False
        for line in display_interface.splitlines():
            if line.startswith("Interface            Link Speed"):
                active = True
                continue
            if active:
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
        """FlexFabric implementation of get_lldp_neighbors."""
        lldp = {}
        command = "display lldp neighbor-information list"
        output = self._send_command(command)
        active = False
        for line in output.splitlines():
            if line.startswith("System Name"):
                active = True
                continue
            if active:
                remote_sys, local_if, _, remote_port = line.split()
                lldp[local_if] = [{"hostname": remote_sys, "port": remote_port}]
        if not lldp:
            for line in output.splitlines():
                if line.startswith("Local Interface"):
                    active = True
                    continue
                if active:
                    split_line = line.split()
                    local_if, remote_port, remote_sys = split_line[0], split_line[-2], split_line[-1]
                    lldp[local_if] = [{"hostname": remote_sys, "port": remote_port}]

        return lldp

    def get_lldp_neighbors_detail(self, interface=""):
        lldp = {}
        lldp_interfaces = []

        if interface:
            command = "display lldp neighbor-information interface {} verbose".format(interface)
        else:
            command = "display lldp neighbor-information verbose"

        lldp_entries = self._send_command(command)

        #TODO

        return {}



    def get_environment(self):

        environment = {}

        cpu_cmd = "display cpu-usage summary"
        mem_cmd = "display memory summary"
        temp_cmd = "display environment"
        fan_cmd = "display fan"
        pwr_cmd = "display power"

        # fan health
        output = self._send_command(fan_cmd)
        environment.setdefault("fans", {})
        active = False
        chassis = 0
        for line in output.splitlines():
            if line.startswith(" ---"):
                active = True
                chassis +=1
                continue
            elif line == "" or line.startswith(" Fan-tray"):
                active = False
                continue
            elif active == False:
                continue
            line_list = line.split()
            if line_list[1] != "Normal":
                fan_state = False
            else:
                fan_state = True
            fan_id = str(chassis) + "_" + line_list[0]
            environment["fans"][fan_id] = {
                "status": fan_state
            }
        if not environment["fans"]:
            for line in output.splitlines():
                if line.startswith("Slot") or line.startswith(" Slot"):
                    chassis +=1
                    continue
                elif "FAN" in line or "Fan " in line:
                    fan_id = str(chassis) + "_" + line.split()[1].strip(":")
                    continue
                elif "State" in line:
                    if line.split(":")[-1].strip() != "Normal":
                        fan_state = False
                    else:
                        fan_state = True
                    environment["fans"][fan_id] = {
                        "status": fan_state
                    }

        # temperature sensors
        output = self._send_command(temp_cmd)
        environment.setdefault("temperature", {})
        if "Slot" in output.splitlines()[0]:
            slot = 0
            active = False
            for line in output.splitlines():
                if "Slot" in line:
                    slot += 1
                    active = False
                    continue
                elif not active and line.startswith("Sensor"):
                    active = True
                    continue
                if active:
                    split_line = line.split()
                    location = str(slot) + "_" + "_".join(split_line[0:2])
                    temperature = float(split_line[2])
                    environment["temperature"][location] = {
                        "temperature": temperature,
                        "is_alert": temperature > float(split_line[-3]),
                        "is_critical": temperature > float(split_line[-2])
                    }
        else:
            if "Chassis" in output.splitlines()[2]:
                marker = 4
            else:
                marker = 3
            for line in output.splitlines()[3:]:
                split_line = line.split()
                location = "_".join(split_line[0:marker])
                temperature = float(split_line[marker])
                environment["temperature"][location] = {
                    "temperature": temperature,
                    "is_alert": temperature > float(split_line[-3]),
                    "is_critical": temperature > float(split_line[-2])
                }

        # power supply units
        # currently not implemented
        environment.setdefault('power', {})
        environment['power']['invalid'] = {'status': True, 'output': -1.0, 'capacity': -1.0}
        #TODO

        # cpu usage
        output = self._send_command(cpu_cmd)
        environment.setdefault("cpu", {})
        usage = 0.0
        if "Wrong parameter found at" in output:
            output = self._send_command("display cpu-usage | include 1 minute")
            for idx, line in enumerate(output.splitlines()):
                environment["cpu"][idx] = {}
                environment["cpu"][idx]["%usage"] = 0.0
                usage = float(line.split()[0].strip("%"))
                environment["cpu"][idx]["%usage"] = usage
        else:
            if "Chassis" in output.splitlines()[0]:
                marker = 4
            else:
                marker = 3
            for idx, line in enumerate(output.splitlines()[1:]):
                environment["cpu"][idx] = {}
                environment["cpu"][idx]["%usage"] = 0.0
                usage = float(line.split()[marker].strip("%"))
                environment["cpu"][idx]["%usage"] = usage

        # memory usage
        output = self._send_command(mem_cmd)
        environment.setdefault("memory", {})
        if "Too many parameters found at" in output:
            output = self._send_command("display memory")
            for line in output.splitlines():
                if "Total Memory" in line:
                    total = int(line.split(":")[-1].strip())
                elif "Used Memory" in line:
                    used = int(line.split(":")[-1].strip())
        else:
            if "Chassis" in output.splitlines()[1]:
                marker = 1
            else:
                marker = 0
            total = 0
            used = 0
            for line in output.splitlines()[2:]:
                total += int(line.split()[2 + marker])
                used += int(line.split()[3 + marker])
        environment["memory"]["used_ram"] = used
        environment["memory"]["available_ram"] = total

        return environment


    def get_config(self, retrieve='all'):
        """get_config implementation for FlexFabric"""
        get_startup = retrieve == "all" or retrieve == "startup"
        get_running = retrieve == "all" or retrieve == "running"

        if retrieve == "all" or get_startup or get_running:
            command1 = "display current-configuration"
            command2 = "display saved-configuration"
            
            output1 = self._send_command(command1)
            output2 = self._send_command(command2)

            return{
                "startup": py23_compat.text_type(output2)
                if get_startup
                else "",
                "running": py23_compat.text_type(output1)
                if get_running
                else "",
                "candidate": ""
            }
        else:
            return {"startup": "", "running": "", "candidate": ""}



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
        command = "display interface"
        display_interface = self._send_command(command)

        interfaces = {}
        name_not_set = True
        for line in display_interface.splitlines():
            if not line:
                name_not_set = True
                continue
            elif name_not_set:
                interface = self._short_interface(line.split()[0])
                interfaces[interface] = {}
                name_not_set = False
            if "Current state:" in line or "current state:" in line:
                state = line.split()[-1]
                if state == "UP":
                    interfaces[interface]["is_up"] = True
                    interfaces[interface]["is_enabled"] = True
                else:
                    interfaces[interface]["is_up"] = False
                    if state == "ADM":
                        interfaces[interface]["is_enabled"] = False
                    else:
                        interfaces[interface]["is_enabled"] = True
            elif line.startswith("Bandwidth:"):
                interfaces[interface]["speed"] = int(line.split()[-2].strip()) * 1e-3
            elif line.startswith("Last link flapping:"):
                interfaces[interface]["last_flapped"] = float(self.parse_uptime(line.split(":")[-1].strip()))
            elif "hardware address" in line or "Hardware Address" in line:
                interfaces[interface]["mac_address"] = napalm.base.helpers.convert(
                    napalm.base.helpers.mac, line.split(":")[-1].strip()
                )
            elif "Description:" in line:
                interfaces[interface]["description"] = line.split(":")[-1].strip()
        return interfaces


    def get_interfaces_counters(self):
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

    @staticmethod
    def _short_interface(interface):
        """
        Remove lower case characters from interface
        name to get standard interface names
        """
        if interface.startswith("Ten-GigabitEthernet"):
            interface = interface.replace("Ten-GigabitEthernet", "XGE")
        elif interface.startswith("FortyGigE"):
            interface = interface.replace("FortyGigE", "FGE")
        elif interface.startswith("M-GigabitEthernet"):
            interface = interface.replace("M-GigabitEthernet","MGE")
        elif interface.startswith("Bridge-Aggregation"):
            interface = interface.replace("Bridge-Aggregation","BAGG")
        elif interface.startswith("HundredGigE"):
            interface = interface.replace("HundredGigE","HGE")
        elif interface.startswith("InLoopBack"):
            interface = interface.replace("InLoopBack","InLoop")
        elif interface.startswith("LoopBack"):
            interface = interface.replace("LoopBack","Loop")
        elif interface.startswith("Multicast Tunnel"):
            interface = interface.replace("Multicast Tunnel","MTunnel")
        elif interface.startswith("Register-Tunnel"):
            interface = interface.replace("Register-Tunnel","REG")
        elif interface.startswith("Route-Aggregation"):
            interface = interface.replace("Route-Aggregation","RAGG")
        elif interface.startswith("SAN-Aggregation"):
            interface = interface.replace("SAN-Aggregation","SAGG")
        elif interface.startswith("S-Channel"):
            interface = interface.replace("S-Channel","S-Ch")
        elif interface.startswith("Schannel-Aggregation"):
            interface = interface.replace("Schannel-Aggregation","SCH-AGG")
        elif interface.startswith("Schannel-Bundle"):
            interface = interface.replace("Schannel-Bundle","SCH-B")
        elif interface.startswith("Tunnel"):
            interface = interface.replace("Tunnel","Tun")
        elif interface.startswith("Vsi-interface"):
            interface = interface.replace("Vsi-interface","Vsi")
        elif interface.startswith("Vlan-interface"):
            interface = interface.replace("Vlan-interface","Vlan-int")
        return interface
