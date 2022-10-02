from mycroft import MycroftSkill, intent_file_handler
from fuzzywuzzy import fuzz
import requests
import json
import socket

__author__ = "GonzRon"


class LCSHubitatIntegration(MycroftSkill):
    def __init__(self):
        super().__init__()
        self.all_devices_dict = dict()
        self.configured = False
        self.dev_commands_dict = None
        self.dev_capabilities_dict = dict()
        self.address = None
        self.attr_dict = None
        self.min_fuzz = None
        self.access_token = None
        self.settings_change_callback = None
        self.hub_devices_retrieved = None
        self.dev_id_dict = None
        self.maker_api_app_id = None

    def initialize(self):
        # This dict will hold the device name and its hubitat id number
        self.dev_id_dict = {}
        self.hub_devices_retrieved = False
        # Get a few settings from the Mycroft website (they are specific to the user site) and
        # get the current values
        self.settings_change_callback = self.on_settings_changed
        self.on_settings_changed()

    def on_settings_changed(self):
        # Fetch the settings from the user account on mycroft.ai
        self.access_token = {'access_token': self.settings.get('access_token')}
        self.address = self.settings.get('local_address')
        self.min_fuzz = self.settings.get('minimum_fuzzy_score')
        self.maker_api_app_id = str(self.settings.get('hubitat_maker_api_app_id'))
        # The attributes are a special case.  I want to end up with a dict indexed by attribute
        # name with the contents being the default device.  But I did not want the user to have
        # to specify this in Python syntax.  So I just have the user give CSVs, possibly in quotes,
        # and the convert them to lists and then to a dict.
        attr_name = self.settings.get('attr_name')
        dev_name = self.settings.get('dev_name')

        if None not in [self.access_token, self.address, self.min_fuzz, self.maker_api_app_id, attr_name, dev_name]:
            # Remove quotes
            attr_name = attr_name.replace('"', '').replace("'", "")
            dev_name = dev_name.replace('"', '').replace("'", "")
            self.log.debug("Settings are " + attr_name + " and " + dev_name)

            # Turn them into lists
            attrs = attr_name.rsplit(",")
            devs = dev_name.rsplit(",")
            # self.log.info("Changed to "+attrs+" and "+devs)

            # Now turn the two lists into a dict and add an attribute for testing
            self.attr_dict = dict(zip(attrs, devs))
            self.attr_dict["testattr"] = "testAttrDev"
            self.log.debug(self.attr_dict)

            # If the device name is local assume it is fairly slow and change it to a dotted quad
            try:
                self.address = socket.gethostbyname(self.address)
                socket.inet_aton(self.address)
            except socket.error:
                self.log.info("Invalid Hostname or IP Address: addr={}".format(self.address))
                return

            self.log.debug(
                f"Updated settings: access token={self.access_token}, fuzzy={self.min_fuzz}, addr={self.address}, "
                f"makerApiId={self.maker_api_app_id}, attr dictionary={self.attr_dict}")
            self.configured = True

    def not_configured(self):
        self.log.debug("Cannot Run Intent - Settings not Configured")
    #
    # Intent handlers
    #

    @intent_file_handler('turn.on.intent')
    def handle_on_intent(self, message):
        # This is for utterances like "turn on the xxx"
        if self.configured:
            self.handle_on_or_off_intent(message, 'on')
        else:
            self.not_configured()

    @intent_file_handler('turn.off.intent')
    def handle_off_intent(self, message):
        # For utterances like "turn off the xxx".  A
        if self.configured:
            self.handle_on_or_off_intent(message, 'off')
        else:
            self.not_configured()

    @intent_file_handler('level.intent')
    def handle_level_intent(self, message):
        if self.configured:
            # For utterances like "set the xxx to yyy%"
            try:
                device = self.get_hub_device_name(message)
            except NameError:
                # g_h_d_n speaks a dialog before throwing an error
                return
            level = message.data.get('level')

            dev_id = self.dev_id_dict[device]
            if self.is_device_capable(device, 'Thermostat'):
                if supported_modes := self.get_device_attribute(dev_id, "supportedThermostatModes"):
                    supported_modes = [s.strip() for s in supported_modes.strip('[]').split(',')]
                    self.log.debug("Set Level Supported Modes: " + str(supported_modes))
                    self.log.debug("Level is: " + str(level))
                    if level in supported_modes and self.is_command_available(dev_id, 'setThermostatMode'):
                        self.hub_command_devices(dev_id, "setThermostatMode", level)
                        self.speak_dialog(str(device) + ' set to ' + str(level), data={'device': device, 'level': level})
                    else:
                        try:
                            val = int(level)
                        except ValueError:
                            raise Exception("Unsupported Value")
                        else:
                            t_stat_mode = self.get_device_attribute(dev_id, "thermostatMode")
                            if 'cool' in t_stat_mode:
                                self.call_set_level(dev_id, level, "setCoolingSetpoint")
                            elif 'heat' in t_stat_mode:
                                self.call_set_level(dev_id, level, "setHeatingSetpoint")
                            else:
                                self.speak_dialog(str(device) + ' is currently set to ' + str(t_stat_mode), data={'device': device, 'level': level})
            elif self.is_command_available(dev_id, 'setLevel'):
                self.call_set_level(device, level)
            else:
                raise Exception("Unsupported Device")
            self.update_devices()
        else:
            self.not_configured()

    def call_set_level(self, device_id, level, mode='setLevel'):
        if self.is_command_available(device_id, mode):
            self.hub_command_devices(device_id, mode, level)
            self.speak_dialog('ok', data={'device': self.all_devices_dict[device_id]['name']})

    @intent_file_handler('attr.intent')
    def handle_attr_intent(self, message):
        if self.configured:
            # This one is for getting device attributes like level or temperature
            try:
                attr = self.hub_get_attr_name(message.data.get('attr'))
            except:
                # Get_attr_name also speaks before throwing an error
                return
            try:
                device = self.get_hub_device_name(message)
            except:
                device = self.get_hub_device_name_from_text(self.attr_dict[attr])

            self.log.debug("Found attribute={},device={}".format(attr, device))
            val = self.get_device_attribute(self.hub_get_device_id(device), attr)
            if val is None:
                self.speak_dialog('attr.not.supported', data={'device': device, 'attr': attr})
            else:
                self.speak_dialog('attr', data={'device': device, 'attr': attr, 'value': val})
        else:
            self.not_configured()

    @intent_file_handler('rescan.intent')
    def handle_rescan_intent(self, message):
        if self.configured:
            self.update_devices()
            device_count = len(self.dev_id_dict)
            self.log.info(str(device_count))
            self.speak_dialog('rescan', data={'count': device_count})
        else:
            self.not_configured()

    @intent_file_handler('list.devices.intent')
    def handle_list_devices_intent(self, message):
        if self.configured:
            if not self.hub_devices_retrieved:
                self.update_devices()
            number = 0
            for label, dev_id in self.dev_id_dict.items():
                # Speak the real devices, but not the test devices
                if '**test' not in dev_id:
                    number += 1
                    self.speak_dialog('list.devices', data={'number': str(number), 'name': label, 'id': dev_id})
        else:
            self.not_configured()

    #
    # Routines used by intent handlers
    #
    def handle_on_or_off_intent(self, message, cmd):
        # Used for both on and off
        try:
            self.log.debug("In on/off intent with command " + cmd)
            device = self.get_hub_device_name(message)
            silence = message.data.get('how')
            device_id = self.dev_id_dict[device]
        except NameError:
            # get_hub_device_name speaks the error dialog
            return

        if self.is_command_available(device_id, cmd):
            try:
                self.hub_command_devices(device_id, cmd)
                if silence is None:
                    self.speak_dialog('ok', data={'device': device})
            except requests.RequestException:
                # If command devices throws an error, probably a bad URL
                self.speak_dialog('url.error')

    def is_command_available(self, device_id, command):
        # Complain if the specified attribute is not one in the Hubitat maker app.
        self.log.debug("In is_command_available with device=" + str(device_id) + ' and command: ' + str(command))

        if command in self.all_devices_dict[device_id]['commands']:
            self.log.debug("command is available")
            return True

        self.speak_dialog('command.not.supported', data={'device': device_id, 'command': command})
        return False

    def is_device_capable(self, device, capability):
        self.log.debug("In is_device_capable with device=" + str(device) + ' searching for capability: ' + str(capability))
        device_id = self.dev_id_dict[device]
        if capability in self.all_devices_dict[device_id]['capabilities']:
            self.log.debug("device is capable")
            return True
        return False

    def get_hub_device_name(self, message):
        # This one looks in an utterance message for 'device' and then passes the text to
        # get_hub_device_name_from_text to see if it is in Hubitat
        self.log.debug("In get_h_d_n with device=")
        utt_device = message.data.get('device')
        self.log.debug(utt_device)
        if utt_device is None:
            raise NameError('NoDevice')
        device_name = self.get_hub_device_name_from_text(utt_device)
        self.log.debug("Device is " + str(device_name))
        return device_name

    def get_hub_device_name_from_text(self, text):
        # Look for a device name in the list of Hubitat devices.
        # The text may have something a bit different from the real name like "the light" or "lights" rather
        # than the actual Hubitat name of light.  This finds the actual Hubitat name using 'fuzzy-wuzzy' and
        # the match score specified as a setting by the user
        if not self.hub_devices_retrieved:
            # In case we never got the devices
            self.update_devices()

        # Here we compare all the Hubitat devices against the requested device using fuzzy and take
        # the device with the highest score that exceeds the minimum
        best_name = None
        best_score = self.min_fuzz
        for hub_dev in self.dev_id_dict:
            score = fuzz.token_sort_ratio(hub_dev, text)
            self.log.debug("Hubitat=" + hub_dev + ", utterance=" + text + " score=" + str(score))
            if score > best_score:
                best_score = score
                best_name = hub_dev
        self.log.debug("Best score is " + str(best_score))
        if best_score > self.min_fuzz:
            self.log.debug("Changed " + text + " to " + best_name)
            return best_name

        # Nothing had a high enough score.  Speak and throw.
        self.log.debug("No device found for " + text)
        self.speak_dialog('device.not.supported', data={'device': text})
        raise Exception("Unsupported Device")

    def hub_get_attr_name(self, name):
        # This is why we need a list of possible attributes, as otherwise we could not do a fuzzy search.
        best_name = None
        best_score = self.min_fuzz
        self.log.debug(self.attr_dict)
        attr = None

        for attr in self.attr_dict:
            self.log.debug("attr is {}".format(attr))
            score = fuzz.token_sort_ratio(attr, name)
            # self.log.info("Hubitat="+hubDev+", utterance="+text+" score="+str(score))
            if score > best_score:
                best_score = score
                best_name = attr

        self.log.debug("Best score is " + str(best_score))
        if best_score > self.min_fuzz:
            self.log.debug("Changed " + attr + " to " + best_name)
            return best_name
        else:
            self.log.debug("No device found for " + name)
            self.speak_dialog('attr.not.supported', data={'device': 'any device in settings', 'attr': name})
            raise Exception("Unsupported Attribute")

    def hub_command_devices(self, dev_id, state, value=None):
        # Build a URL to send the requested command to the Hubitat and
        # send it via "access_hubitat".  Some commands also have a value like "setlevel"
        if dev_id[0:6] == "**test":
            # This is used for regression tests only
            return
        url = "/apps/api/" + self.maker_api_app_id + "/devices/" + dev_id + "/" + state  # This URL is as specified in Hubitat maker app
        if value:
            url = url + "/" + value
        self.log.debug("URL for switching device " + url)
        try:
            self.access_hubitat(url)
        except requests.RequestException as e:
            raise e

    def get_device_attribute(self, dev_id, attr):
        self.log.debug("Looking for attr {}".format(attr))
        # The json string from Hubitat turns into a dict.  The key attributes
        # has a value of a list.  The list is a list of dicts with the attribute
        # name, value, and other things that we don't care about.  So here when
        # the device was a test device, we fake out the attributes for testing
        if dev_id == "**testAttr":
            tempList = [{'name': "testattr", "currentValue": 99}]
            jsn = {"attributes": tempList}
            x = jsn["attributes"]

        try:
            return self.all_devices_dict[dev_id]['attributes'][attr]
        except KeyError:
            self.log.debug("Attribute not found")
            return None

    def update_devices(self):
        # Init the device list and command list with tests
        self.dev_commands_dict = {"testOnDev": ["on"], "testOnOffDev": ["on", "off"],
                                  "testLevelDev": ["on", "off", "setLevel"]}
        self.dev_id_dict = {"testOnDev": "**testOnOff", "testOnOffDev": "**testOnOff", "testLevelDev": "**testLevel",
                            "testAttrDev": "**testAttr"}
        self.log.debug(self.access_token)

        # Now get the actual devices from Hubitat and parse out the devices and their IDs and valid
        # commands
        request = self.access_hubitat("/apps/api/" + self.maker_api_app_id + "/devices/all")
        try:
            json_data = json.loads(request)
        except json.JSONDecodeError:
            self.log.debug("Error on json load")
            return

        self.all_devices_dict = {device['id']: device for device in json_data}
        self.dev_id_dict = {device['label']: device['id'] for device in json_data}

        for k, v in self.all_devices_dict.items():
            self.all_devices_dict[k]['commands'] = [command['command'] for command in v['commands']]

        self.hub_devices_retrieved = True
        return len(self.all_devices_dict)

    def access_hubitat(self, part_url):
        # This routine knows how to talk to the hubitat.  It builds the URL from
        # the know access type (http://) and the domain info or dotted quad in
        # self.address, followed by the command info passed in by the caller.

        url = "http://" + self.address + part_url
        try:
            request = requests.get(url, params=self.access_token, timeout=5)
        except requests.RequestException:
            # If the request throws an error, the address may have changed.  Try
            # 'hubitat.local' as a backup.
            try:
                self.speak_dialog('url.backup')
                self.address = socket.gethostbyname("hubitat.local")
                url = "http://" + self.address + part_url
                self.log.debug("Fell back to hubitat.local which translated to " + self.address)
                request = requests.get(url, params=self.access_token, timeout=10)
            except requests.RequestException as e:
                self.log.debug("Got an error from requests: " + str(e))
                self.speak_dialog('url.error')
                raise e
        return request.text
