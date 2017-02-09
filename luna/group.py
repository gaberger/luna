'''
Written by Dmitry Chirikov <dmitry@chirikov.ru>
This file is part of Luna, cluster provisioning tool
https://github.com/dchirikov/luna

This file is part of Luna.

Luna is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Luna is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Luna.  If not, see <http://www.gnu.org/licenses/>.

'''

from config import *

import logging

from bson.dbref import DBRef
from bson.objectid import ObjectId

from luna import utils
from luna.base import Base
from luna.cluster import Cluster
from luna.network import Network
from luna.osimage import OsImage
from luna.bmcsetup import BMCSetup


class Group(Base):
    """Class for operating with group records"""

    log = logging.getLogger(__name__)

    def __init__(self, name=None, mongo_db=None, create=False, id=None,
                 prescript=None, bmcsetup=None, bmcnetwork=None,
                 partscript=None, osimage=None, interfaces=None,
                 postscript=None, boot_if=None, torrent_if=None):
        """
        prescript   - preinstall script
        bmcsetup    - bmcsetup options
        bmcnetwork  - used for bmc networking
        partscript  - parition script
        osimage     - osimage
        interfaces  - list of the newtork interfaces
        postscript  - postinstall script
        """

        self.log.debug("function args {}".format(self._debug_function()))

        # Define the schema used to represent node objects

        self._collection_name = 'group'
        self._keylist = {'prescript': type(''), 'partscript': type(''),
                         'postscript': type(''), 'boot_if': type(''),
                         'torrent_if': type('')}

        # Check if this group is already present in the datastore
        # Read it if that is the case

        group = self._get_object(name, mongo_db, create, id)

        if create:
            cluster = Cluster(mongo_db=self._mongo_db)
            osimageobj = OsImage(osimage)

            (bmcobj, bmcnetobj) = (None, None)
            if bmcsetup:
                bmcobj = BMCSetup(bmcsetup).DBRef

            if bmcnetwork:
                bmcnetobj = Network(bmcnetwork, mongo_db=self._mongo_db).DBRef

            if bool(interfaces) and type(interfaces) is not type([]):
                self.log.error("'interfaces' should be list")
                raise RuntimeError
            if_dict = {}
            if not bool(interfaces):
                interfaces = []
            for interface in interfaces:
                if_dict[interface] = {'network': None, 'params': ''}
            if not bool(partscript):
                partscript = "mount -t tmpfs tmpfs /sysroot"
            if not bool(prescript):
                prescript = ""
            if not bool(postscript):
                postscript = """cat <<EOF>>/sysroot/etc/fstab
tmpfs   /       tmpfs    defaults        0 0
EOF"""

            # Store the new group in the datastore

            group = {'name': name, 'prescript':  prescript, 'bmcsetup': bmcobj,
                     'bmcnetwork': bmcnetobj, 'partscript': partscript,
                     'osimage': osimageobj.DBRef, 'interfaces': if_dict,
                     'postscript': postscript, 'boot_if': boot_if,
                     'torrent_if': torrent_if}

            self.log.debug("Saving group '{}' to the datastore".format(group))

            self.store(group)

            # Link this group to its dependencies and the current cluster

            self.link(cluster)

            if bmcobj:
                self.link(bmcobj)

            if bmcnetobj:
                self.link(bmcnetobj)

            self.link(osimageobj)

        self.log = logging.getLogger('group.' + self._name)

    def osimage(self, osimage_name):
        if not self._id:
            self.log.error("Was object deleted?")
            return None
        osimage = OsImage(osimage_name)
        old_dbref = self._get_json()['osimage']
        self.unlink(old_dbref)
        res = self._mongo_collection.update({'_id': self._id}, {'$set': {'osimage': osimage.DBRef}}, multi=False, upsert=False)
        self.link(osimage.DBRef)
        return not res['err']

    def bmcsetup(self, bmcsetup_name):
        if not self._id:
            self.log.error("Was object deleted?")
            return None
        bmcsetup = None
        if bool(bmcsetup_name):
            bmcsetup = BMCSetup(bmcsetup_name)
        old_dbref = self._get_json()['bmcsetup']
        if bool(old_dbref):
            self.unlink(old_dbref)
        if bool(bmcsetup):
            res = self._mongo_collection.update({'_id': self._id}, {'$set': {'bmcsetup': bmcsetup.DBRef}}, multi=False, upsert=False)
            self.link(bmcsetup.DBRef)
        else:
            res = self._mongo_collection.update({'_id': self._id}, {'$set': {'bmcsetup': None}}, multi=False, upsert=False)
        return not res['err']

    def set_bmcnetwork(self, bmcnet):
        old_bmcnet_dbref = self._get_json()['bmcnetwork']
        net = Network(bmcnet, mongo_db = self._mongo_db)
        reverse_links = self.get_back_links()
        if bool(old_bmcnet_dbref):
            self.log.error("Network is already defined for BMC interface")
            return None
        res = self._mongo_collection.update({'_id': self._id}, {'$set': {'bmcnetwork': net.DBRef}}, multi=False, upsert=False)
        self.link(net.DBRef)
        for link in reverse_links:
            if link['collection'] != 'node':
                continue
            node = Node(id=link['DBRef'].id, mongo_db = self._mongo_db)
            node.add_ip(bmc=True)
        return not res['err']

    def del_bmcnetwork(self):
        old_bmcnet_dbref = self._get_json()['bmcnetwork']
        if bool(old_bmcnet_dbref):
            reverse_links = self.get_back_links()
            for link in reverse_links:
                if link['collection'] != 'node':
                    continue
                node = Node(id=link['DBRef'].id, mongo_db = self._mongo_db)
                node.del_ip(bmc=True)
            self.unlink(old_bmcnet_dbref)
        res = self._mongo_collection.update({'_id': self._id}, {'$set': {'bmcnetwork': None}}, multi=False, upsert=False)
        return not res['err']


    def show_bmc_if(self, brief = False):
        bmcnetwork = self._get_json()['bmcnetwork']
        if not bool(bmcnetwork):
            return ''
        (NETWORK, PREFIX) = ("", "")
        try:
            net = Network(id = bmcnetwork.id, mongo_db = self._mongo_db)
            NETWORK = net.get('NETWORK')
            PREFIX =  str(net.get('PREFIX'))
        except:
            pass
        if brief:
            return "[" +net.name + "]:"+ NETWORK + "/" + PREFIX
        return NETWORK + "/" + PREFIX

    def get_net_name_for_if(self, interface):
        interfaces = self._get_json()['interfaces']
        try:
            params = interfaces[interface]
        except:
            self.log.error("Interface '{}' does not exist".format(interface))
            return ""
        try:
            net = Network(id = params['network'].id, mongo_db = self._mongo_db)
        except:
            net = None
        if bool(net):
            return net.name
        return ""

    def show_if(self, interface, brief = False):
        interfaces = self._get_json()['interfaces']
        try:
            params = interfaces[interface]
        except:
            self.log.error("Interface '{}' does not exist".format(interface))
            return ""
        (outstr, NETWORK, PREFIX) = ("", "", "")
        try:
            net = Network(id = params['network'].id, mongo_db = self._mongo_db)
            NETWORK = net.get('NETWORK')
            PREFIX =  str(net.get('PREFIX'))
        except:
            pass
        if NETWORK:
            if brief:
                return "[" +net.name + "]:" + NETWORK + "/" + PREFIX
            outstr = "NETWORK=" + NETWORK + "\n"
            outstr += "PREFIX=" + PREFIX
        if params['params'] and not brief:
            outstr += "\n" + params['params']
        return outstr.rstrip()

    def add_interface(self, interface):
        if not self._id:
            self.log.error("Was object deleted?")
            return None
        interfaces = self._get_json()['interfaces']
        old_parms = None
        try:
            old_parms = interfaces[interface]
        except:
            pass
        if bool(old_parms):
            self.log.error("Interface already exists")
            return None
        interfaces[interface] = {'network': None, 'params': ''}
        res = self._mongo_collection.update({'_id': self._id}, {'$set': {'interfaces': interfaces}}, multi=False, upsert=False)
        if res['err']:
            self.log.error("Error adding interface '{}'".format(interface))
            return None
        return True

    def get_if_parms(self, interface):
        interfaces = self._get_json()['interfaces']
        try:
            interfaces[interface]
        except:
            self.log.error("Interface '{}' does not exist".format(interface))
            return None
        return interfaces[interface]['params']

    def list_interfaces(self):
        json = self._get_json()
        try:
            interfaces = json['interfaces']
        except:
            self.log.error("No interfaces for group '{}' configured.".format(self.name))
            interfaces = {}
        try:
            bmcnet = json['bmcnetwork']
        except:
            self.log.error("No network for BMC interface for group '{}' configured.".format(self.name))
            bmcnet = None
        return {'interfaces': interfaces, 'bmcnetwork': bmcnet}

    def get_rel_ips_for_net(self, netobjid):
        rel_ips = {}

        def add_to_dict(key, val):
            try:
                rel_ips[key]
                self.log.error("Duplicate ip detected in '{}'. Can not put '{}'".format(self.name, key))
            except:
                rel_ips[key] = val

        json = self._get_json()
        if_dict = self.list_interfaces()
        bmcif =  if_dict['bmcnetwork']
        ifs = if_dict['interfaces']
        if bool(bmcif):
            if bmcif.id == netobjid:
                try:
                    node_links = json[usedby_key]
                except KeyError:
                    node_links = None
                if bool(node_links):
                    for node_id in node_links['node']:
                        node = Node(id = ObjectId(node_id))
                        add_to_dict(node.name, node.get_ip(bmc=True, format='num'))

        if bool(if_dict):
            for interface in ifs:
                if not bool(ifs[interface]['network']):
                    continue
                if ifs[interface]['network'].id == netobjid:
                    try:
                        node_links = json[usedby_key]
                    except KeyError:
                        node_links = None
                    if not bool(node_links):
                        continue
                    for node_id in node_links['node']:
                        node = Node(id = ObjectId(node_id))
                        add_to_dict(node.name, node.get_ip(interface, format='num'))
        return rel_ips


    def set_if_parms(self, interface, parms = ''):
        interfaces = self._get_json()['interfaces']
        try:
            interfaces[interface]
        except:
            self.log.error("Interface '{}' does not exist".format(interface))
            return None
        interfaces[interface]['params'] = parms.strip()
        res = self._mongo_collection.update({'_id': self._id}, {'$set': {'interfaces': interfaces}}, multi=False, upsert=False)
        if res['err']:
            self.log.error("Error setting network parameters for interface '{}'".format(interface))
            return None
        return True

    def set_net_to_if(self, interface, network):
        interfaces = self._get_json()['interfaces']
        net = Network(network, mongo_db = self._mongo_db)
        try:
            old_parms = interfaces[interface]
        except:
            old_parms = None
            self.log.error("Interface '{}' does not exist".format(interface))
            return None
        try:
            old_net = old_parms['network']
        except:
            old_net = None
        if bool(old_net):
            self.log.error("Network is already defined for this interface '{}'".format(interface))
            return None
        interfaces[interface]['network'] = net.DBRef
        res = self._mongo_collection.update({'_id': self._id}, {'$set': {'interfaces': interfaces}}, multi=False, upsert=False)
        if res['err']:
            self.log.error("Error adding network for interface '{}'".format(interface))
            return None
        self.link(net.DBRef)
        reverse_links = self.get_back_links()
        for link in reverse_links:
            if link['collection'] != 'node':
                continue
            node = Node(id=link['DBRef'].id, mongo_db = self._mongo_db)
            node.add_ip(interface)
        return True

    def del_net_from_if(self, interface):
        interfaces = self._get_json()['interfaces']
        try:
            old_parms = interfaces[interface]
        except:
            old_parms = None
            self.log.error("Interface '{}' does not exist".format(interface))
            return None
        try:
            net_dbref = old_parms['network']
        except:
            net_dbref = None
            self.log.error("Network is not configured for interface '{}'".format(interface))
            return None
        if not bool(net_dbref):
            self.log.error("Network is not configured for interface '{}'".format(interface))
            return None
        reverse_links = self.get_back_links()
        for link in reverse_links:
            if link['collection'] != 'node':
                continue
            node = Node(id=link['DBRef'].id, mongo_db = self._mongo_db)
            node.del_ip(interface)
        self.unlink(net_dbref)
        interfaces[interface]['network'] = None
        res = self._mongo_collection.update({'_id': self._id}, {'$set': {'interfaces': interfaces}}, multi=False, upsert=False)
        if res['err']:
            self.log.error("Error adding network for interface '{}'".format(interface))
            return None
        return True

    def del_interface(self, interface):
        self.del_net_from_if(interface)
        interfaces = self._get_json()['interfaces']
        interfaces.pop(interface)
        res = self._mongo_collection.update({'_id': self._id}, {'$set': {'interfaces': interfaces}}, multi=False, upsert=False)
        if res['err']:
            self.log.error("Error deleting interface '{}'".format(interface))
            return None
        return True

    def _manage_ip(self, interface=None, ip=None, bmc=False, release=False):
        if bmc:
            net_dbref = self._json['bmcnetwork']
        elif self._json['interfaces'] and interface in self._json['interfaces']:
            net_dbref = self._json['interfaces'][interface]['network']
        else:
            net_dbref = None

        if not net_dbref:
            self.log.warning(("Non-existing or unconfigured {} interface"
                              .format(interface or 'BMC')))
            return None

        net = Network(id=net_dbref.id, mongo_db=self._mongo_db)

        if release and ip:
            return net.release_ip(ip)

        else:
            return net.reserve_ip(ip)

        return True

    def get_ip(self, interface, ip, bmc=False, format='num'):
        if bmc:
            net_dbref = self._json['bmcnetwork']
        elif self._json['interfaces'] and interface in self._json['interfaces']:
            net_dbref = self._json['interfaces'][interface]['network']
        else:
            net_dbref = None

        if not net_dbref:
            self.log.warning(("Non-existing or unconfigured {} interface"
                              .format(interface or 'BMC')))
            return None

        net = Network(id=net_dbref.id, mongo_db=self._mongo_db)

        if ip and format is 'human':
            return utils.ip.reltoa(net._json['NETWORK'], ip)
        elif ip and format is 'num':
            return utils.ip.atorel(ip, net._json['NETWORK'],
                                   net._json['PREFIX'])

        return None

    @property
    def boot_params(self):
        params = {}
        params['boot_if'] = None
        params['net_prefix'] = None
        osimage = OsImage(id=self.get('osimage').id, mongo_db=self._mongo_db)

        try:
            params['kernel_file'] = osimage.get('kernfile')
        except:
            params['kernel_file'] = ""
        try:
            params['initrd_file'] = osimage.get('initrdfile')
        except:
            params['initrd_file'] = ""
        try:
            params['kern_opts'] = osimage.get('kernopts')
        except:
            params['kern_opts'] = ""
        try:
            params['boot_if'] = self.get('boot_if')
        except:
            params['boot_if'] = ""
            params['net_prefix'] = ""
            return params

        interfaces = self._get_json()['interfaces']
        try:
            if_params = interfaces[params['boot_if']]
        except:
            self.log.error(("Unknown boot interface '{}'. Must be one of '{}'"
                            .format(params['boot_if'], interfaces.keys())))
            params['boot_if'] = ""
            params['net_prefix'] = ""
            return params
        net = None
        try:
            if_net = if_params['network']
            net = Network(id = if_net.id, mongo_db = self._mongo_db)
        except:
            pass
        if not bool(net):
            self.log.error(("Boot interface '{}' has no network configured"
                            .format(params['boot_if'])))
            params['boot_if'] = ""
            params['net_prefix'] = ""
            return params

        params['net_prefix'] = net.get('PREFIX')
        return params

    @property
    def install_params(self):
        params = {}
        params['prescript'] = self.get('prescript')
        params['partscript'] = self.get('partscript')
        params['postscript'] = self.get('postscript')
        try:
            params['boot_if'] = self.get('boot_if')
        except:
            params['boot_if'] = ''
        try:
            params['torrent_if'] = self.get('torrent_if')
        except:
            params['torrent_if'] = ''
        json = self._get_json()
        if bool(params['torrent_if']):
            try:
                net_dbref = json['interfaces'][params['torrent_if']]['network']
                net = Network(id = net_dbref.id, mongo_db = self._mongo_db)
                params['torrent_if_net_prefix'] = str(net.get('PREFIX'))
            except:
                params['torrent_if'] = ''
        try:
            net_dbref = json['interfaces'][self.get('boot_if')]['network']
            net = Network(id = net_dbref.id, mongo_db = self._mongo_db)
            params['domain'] = str(net.name)
        except:
            params['domain'] = ""
        params['interfaces'] = {}
        try:
            interfaces = json['interfaces'].keys()
            for interface in interfaces:
                params['interfaces'][str(interface)] = str(self.get_if_parms(interface))
        except:
            pass
        try:
            interfaces = json['interfaces'].keys()
        except:
            interfaces = []

        for interface in interfaces:
            net_dbref = json['interfaces'][interface]['network']
            try:
                net = Network(id = net_dbref.id, mongo_db = self._mongo_db)
                net_prefix = "\n" + "PREFIX=" + str(net.get('PREFIX'))
            except:
                net_prefix = ""
            params['interfaces'][str(interface)] = params['interfaces'][str(interface)].strip() + net_prefix
        osimage = OsImage(id = self.get('osimage').id, mongo_db = self._mongo_db)
        try:
            params['torrent'] = osimage.get('torrent') + ".torrent"
            params['tarball'] = osimage.get('tarball') + ".tgz"
        except:
            params['torrent'] = ""
            params['tarball'] = ""
        params['kernver'] = osimage.get('kernver')
        params['kernopts'] = osimage.get('kernopts')
        params['bmcsetup'] = {}
        if self.get('bmcsetup'):
            bmcsetup = BMCSetup(id = self.get('bmcsetup').id, mongo_db = self._mongo_db)
            params['bmcsetup']['mgmtchannel'] = bmcsetup.get('mgmtchannel') or 1
            params['bmcsetup']['netchannel'] = bmcsetup.get('netchannel') or 1
            params['bmcsetup']['userid'] = bmcsetup.get('userid') or 3
            params['bmcsetup']['user'] = bmcsetup.get('user') or "ladmin"
            params['bmcsetup']['password'] = bmcsetup.get('password') or "ladmin"
            try:
                net_dbref = json['bmcnetwork']
                net = Network(id = net_dbref.id, mongo_db = self._mongo_db)
                params['bmcsetup']['netmask'] = net.get('NETMASK')
            except:
                params['bmcsetup']['netmask'] = ''
        return params

from luna.node import Node
