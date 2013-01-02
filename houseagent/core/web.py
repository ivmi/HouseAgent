import sys
import houseagent
import datetime
import json
import os.path
import imp
from twisted.internet import reactor
from twisted.web.server import Site
from twisted.web.static import File
from pyrrd.rrd import RRD
from mako.lookup import TemplateLookup
from twisted.internet import defer
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.error import CannotListenError
from twisted.web.resource import Resource, NoResource
from twisted.web.server import NOT_DONE_YET
from uuid import uuid4
from twisted.web import http, resource
from houseagent.core.history import HistoryViewer
            
class Web(object):
    '''
    This class provides the core web interface for HouseAgent.
    All management functions to control HouseAgent take place from here.
    '''
    
    def __init__(self, log, host, port, backlog, coordinator, eventengine, database):
        '''
        Initialize the web interface.
        @param port: the port on which the web server should listen
        @param coordinator: an instance of the network coordinator in order to interact with it
        @param eventengine: an instance of the event engine in order to interact with it
        @param database: an instance of the database layer in order to interact with it
        '''
        self.host = host # web server interface
        self.port = port # web server listening port
        self.backlog = backlog # size of the listen queue
        self.coordinator = coordinator
        self.eventengine = eventengine
        self.db = database
        
        self.log = log

        root = Resource()
        site = Site(root)
        
        # Main page
        root.putChild("", Root())
        
        # Location management
        root.putChild('locations', Locations(self.db))
        root.putChild('locations_view', Locations_view())
        
        # Plugin management
        root.putChild('plugins', Plugins(self.db, self.coordinator))
        root.putChild('plugins_view', Plugins_view())
        
        # Device management
        root.putChild('devices', Devices(self.db))
        root.putChild('devices_view', Devices_view())
        
        # Device control
        root.putChild("control", Control(self.db))

        # Value management
        root.putChild('values', Values(self.db, self.coordinator))
        root.putChild('values_view', Values_view())
        root.putChild('history_types', HistoryTypes(self.db)) 
        root.putChild('history_periods', HistoryPeriods(self.db))
        root.putChild('control_types', ControlTypes(self.db))

        # Events
        root.putChild("event_create", Event_create(self.db))
        root.putChild("event_value_by_id", Event_value_by_id(self.db))
        root.putChild("event_getvalue", Event_getvalue(self.db))
        root.putChild("event_save", Event_save(self.eventengine, self.db))
        root.putChild("event_control_values_by_id", Event_control_values_by_id(self.db))
        root.putChild("event_control_types_by_id", Event_control_types_by_id(self.db))
        root.putChild("events", Events(self.db))
        root.putChild("event_del", Event_del(self.eventengine, self.db))

        # Graphing
        root.putChild("create_graph", CreateGraph(self.db))
        root.putChild("graph_latest", GraphLatest(self.db))
        root.putChild("graph_daily", GraphDaily(self.db))

        # Static files
        root.putChild("css", File(os.path.join(houseagent.template_dir, 'css')))
        root.putChild("js", File(os.path.join(houseagent.template_dir, 'js')))
        root.putChild("images", File(os.path.join(houseagent.template_dir, 'images')))

        # Load plugin pages
        self.load_pages(root)
        
        try:
            reactor.listenTCP(self.port, site, self.backlog, self.host)
        except CannotListenError,e:
            log.critical("--> %s" % e)
            sys.exit(1)

    def load_pages(self, root):
        '''
        This function dynamically loads pages from plugins.
        A pages.py file with atleast the init_pages() function must exist in the 
        plugins/<pluginname>/ folder.
        @return: an array of loaded modules
        '''
        if hasattr(sys, 'frozen'):
            plugin_dir = os.path.join(os.path.dirname(sys.executable), "plugins")
        else:
            plugin_dir = os.path.join(os.path.dirname(houseagent.__file__), "plugins")
        plugin_dirs = os.listdir(plugin_dir)
        
        for dir in plugin_dirs:
            if os.path.isdir(os.path.join(plugin_dir, dir)):
                self.log.debug("--> Plugin directory found, directory: %s" % dir)
                try:
                    file, pathname, description = imp.find_module("pages", [os.path.join(plugin_dir, dir)])                
                    mod = imp.load_module("pages", file, pathname, description)
                    mod.init_pages(root, self.coordinator, self.db)
                    self.log.debug("--> Loaded pages for plugin %s" % dir)
                except ImportError:
                    self.log.warning("--> Warning cannot load pages module for %s, no pages.py file?" % dir)

class Root(Resource):
    '''
    This is the main page for HouseAgent.
    '''
    def render_GET(self, request):
        lookup = TemplateLookup(directories=[houseagent.template_dir])
        template = lookup.get_template('index.html')
        return str(template.render())
    
class HouseAgentREST(Resource):
    '''
    This class implements a basic REST interface.
    '''
    def __init__(self, db):
        Resource.__init__(self)
        self.db = db
        self._load()
        self._objects = []
    
    # functions that must be implemented
    def _load(self, **kwargs):
        raise NotImplementedError
    
    def _add(self, **kwargs):
        raise NotImplementedError
    
    def _edit(self, **kwargs):
        raise NotImplementedError
        
    # internal functions
    def render_GET(self, request):
        output = []
        for obj in self._objects:
            output.append(obj.json())

        return json.dumps(output)
    
    def _done(self):
        self.request.finish()
        
    def _reload(self):
        self._objects = []
        self._load()
    
    def render_POST(self, request):
        self.request = request
        self._add(request.args)
        return NOT_DONE_YET
    
    def render_PUT(self, request):
        self.request = request
        self._edit(http.parse_qs(request.content.read(), 1)) # http://twistedmatrix.com/pipermail/twisted-web/2007-March/003338.html
        return NOT_DONE_YET
           
    def getChild(self, name, request):
        for obj in self._objects:
            if name == str(obj.id):
                return obj
            
        return NoResource(message="The resource %s was not found" % request.URLPath())

class Location(Resource):
    '''
    This object represents a Location.
    '''
    def __init__(self, id, name, parent_name, parent):
        Resource.__init__(self)
        self.id = id
        self.name = name
        self.parent_name = parent_name
        self.parent = parent
        
    def json(self):
        return {'id': self.id, 'name': self.name, 'parent': self.parent_name}
    
    def render_GET(self, request):
        return json.dumps(self.json())
    
    def render_DELETE(self, request):
        self.request = request
        self.parent.delete(self)
        return NOT_DONE_YET

class Locations(HouseAgentREST):

    @inlineCallbacks            
    def _load(self):
        '''
        Load locations from the database.
        '''
        self._objects = []
        location_query = yield self.db.query_locations()
        
        for location in location_query:
            loc = Location(location[0], location[1], location[2], self)
            self._objects.append(loc)
    
    @inlineCallbacks
    def _add(self, parameters):
        try:
            parent = parameters['parent'][0]
        except KeyError:
            parent = None
        
        yield self.db.add_location(parameters['name'][0], parent)
        self._reload()
        self._done()
    
    @inlineCallbacks
    def _edit(self, parameters):
        try:
            parent = parameters['parent'][0]
            if parent == '':
                parent = None
        except KeyError:
            parent = None
        
        yield self.db.update_location(parameters['id'][0], parameters['name'][0], parent)
        self._reload()
        self._done()
    
    @inlineCallbacks
    def delete(self, obj):
        yield self.db.del_location(int(obj.id))
        self._objects.remove(obj)
        obj.request.finish()

class Locations_view(Resource):
    
    def render_GET(self, request):
        lookup = TemplateLookup(directories=[houseagent.template_dir])
        template = lookup.get_template('locations.html')
        return str(template.render())

class Plugin(Resource):
    '''
    This object represents a Plugin.
    '''
    def __init__(self, id, name, authcode, location, parent):
        Resource.__init__(self)
        self.id = id
        self.name = name
        self.authcode = authcode
        self.location = location
        self.parent = parent
        self.status = False
        
    def json(self):
        return {'id': self.id, 'name': self.name, 'authcode': self.authcode, 'location': self.location, 'status': self.status}
    
    def render_GET(self, request):
        return json.dumps(self.json())
    
    def render_DELETE(self, request):
        self.request = request
        self.parent.delete(self)
        return NOT_DONE_YET
    
class Plugins(HouseAgentREST):

    def __init__(self, db, coordinator):
        HouseAgentREST.__init__(self, db)
        self.coordinator = coordinator
        
    def render_GET(self, request):
        ''' 
        This gets overriden in order to support online/offline status
        '''
        output = []
        for obj in self._objects:
            for p in self.coordinator.plugins:
                if p.guid == obj.authcode:
                    obj.status = p.online            
            
            output.append(obj.json())

        return json.dumps(output)

    @inlineCallbacks            
    def _load(self):
        '''
        Load plugins from the database.
        '''
        self._objects = []
        plugin_query = yield self.db.query_plugins()
        
        for plugin in plugin_query:
            plug = Plugin(plugin[2], plugin[0], plugin[1], plugin[3], self)
            self._objects.append(plug)
    
    @inlineCallbacks
    def _add(self, parameters):
        try:
            location = parameters['location'][0]
        except KeyError:
            location = None
            
        uuid = uuid4()    
        yield self.db.register_plugin(parameters['name'][0], uuid, location)
        self._reload()
        self._done()
    
    @inlineCallbacks
    def _edit(self, parameters):
        try:
            location = parameters['location'][0]
        except KeyError:
            location = None
        
        yield self.db.update_plugin(parameters['id'][0], parameters['name'][0], location)
        self._reload()
        self._done()
    
    @inlineCallbacks
    def delete(self, obj):
        yield self.db.del_plugin(int(obj.id))
        self._objects.remove(obj)
        obj.request.finish()
        
class Plugins_view(Resource):
    
    def render_GET(self, request):
        lookup = TemplateLookup(directories=[houseagent.template_dir])
        template = lookup.get_template('plugins.html')
        return str(template.render())

class Device(Resource):
    '''
    This object represents a Device.
    '''
    def __init__(self, id, name, address, plugin, location, parent):
        Resource.__init__(self)
        self.id = id
        self.name = name
        self.address = address
        self.plugin = plugin
        self.location = location
        self.parent = parent
        
    def json(self):
        return {'id': self.id, 'name': self.name, 'address': self.address, 'plugin': self.plugin, 'location': self.location}
    
    def render_GET(self, request):
        return json.dumps(self.json())
    
    def render_DELETE(self, request):
        self.request = request
        self.parent.delete(self)
        return NOT_DONE_YET
    
class Devices(HouseAgentREST):

    @inlineCallbacks            
    def _load(self):
        '''
        Load plugins from the database.
        '''
        self._objects = []
        device_query = yield self.db.query_devices()
        
        for device in device_query:
            dev = Device(device[0], device[1], device[2], device[3], device[4], self)
            self._objects.append(dev)
    
    @inlineCallbacks
    def _add(self, parameters):  
        yield self.db.save_device(parameters['name'][0], parameters['address'][0], parameters['plugin'][0], parameters['location'][0])
        self._reload()
        self._done()
    
    @inlineCallbacks
    def _edit(self, parameters):       
        yield self.db.save_device(parameters['name'][0], parameters['address'][0], parameters['plugin'][0], parameters['location'][0], parameters['id'][0])
        self._reload()
        self._done()
    
    @inlineCallbacks
    def delete(self, obj):
        yield self.db.del_device(int(obj.id))
        self._objects.remove(obj)
        obj.request.finish()
        
class Devices_view(Resource):
    
    def render_GET(self, request):
        lookup = TemplateLookup(directories=[houseagent.template_dir])
        template = lookup.get_template('devices.html')
        return str(template.render())

class Value(Resource):
    '''
    This object represents a Value.
    '''
    def __init__(self, id, name, value, device, device_address, location, plugin, lastupdate, history_type, history_period, control_type, plugin_id, label, parent):
        Resource.__init__(self)
        self.id = id
        self.name = name
        self.value = value
        self.device = device
        self.device_address = device_address
        self.location = location
        self.plugin = plugin
        self.lastupdate = lastupdate
        self.history_type = history_type
        self.history_period = history_period
        self.control_type = control_type
        self.plugin_id = plugin_id
        self.label = label
        self.parent = parent
        
    def json(self):
        return {'id': self.id, 'name': self.name, 'value': self.value, 'device': self.device, 'device_address': self.device_address,
                'location': self.location, 'plugin': self.plugin, 'lastupdate': self.lastupdate, 'history_type': self.history_type,
                'control_type': self.control_type, 'history_period': self.history_period, 'plugin_id': self.plugin_id, 'label': self.label}
    
    def render_GET(self, request):
        return json.dumps(self.json())
    
    def render_DELETE(self, request):
        self.request = request
        self.parent.delete(self)
        return NOT_DONE_YET
    
class ValueActionResult(Resource):
    
    def __init__(self, plugin_id, device_address, value_id, coordinator, action, params):
        Resource.__init__(self)
        self.plugin_id = plugin_id
        self.device_address = device_address
        self.value_id = value_id
        self.coordinator = coordinator
        self.action = action
        self.params = params
        
    def render_GET(self, request):

        def control_result(result):
            request.write(str(result))
            request.finish()
        
        plugin_guid = self.coordinator.plugin_guid_by_id(self.plugin_id)
        
        if self.action == 'poweron':
            self.coordinator.send_poweron(plugin_guid, self.device_address, self.value_id).addCallback(control_result)
        elif self.action == 'poweroff':
            self.coordinator.send_poweroff(plugin_guid, self.device_address, self.value_id).addCallback(control_result)
        elif self.action == 'fire':
            self.coordinator.send_fire(plugin_guid, self.device_address, self.value_id).addCallback(control_result)
        elif self.action == 'dim':
            self.coordinator.send_dim(plugin_guid, self.device_address, self.params["level"], self.value_id).addCallback(control_result)
        elif self.action == 'thermostat_setpoint':
            self.coordinator.send_thermostat_setpoint(plugin_guid, self.device_address, self.params["temp"], self.value_id).addCallback(control_result)
        return NOT_DONE_YET
    
class Values(HouseAgentREST):

    def __init__(self, db, coordinator):
        HouseAgentREST.__init__(self, db)
        self.coordinator = coordinator

    def render_GET(self, request):
        self._objects = []
        self._load().addCallback(self.done)

        self.request = request

        return NOT_DONE_YET
    
    def done(self, result):
    
        output = []
        for obj in self._objects:
            output.append(obj.json())

        self.request.write(json.dumps(output))
        self.request.finish()
    
    @inlineCallbacks            
    def _load(self):
        '''
        Load values from the database.
        '''
        self._objects = []
        value_query = yield self.db.query_values()
        
        for value in value_query:
            val = Value(value[7], value[0], value[1], value[2], value[5], value[6], value[4], value[3], value[10], value[11], value[8], value[12], value[13], self)
            self._objects.append(val)
    
    @inlineCallbacks
    def _edit(self, parameters):       
        yield self.db.save_value(parameters['label'][0], parameters['history_type'][0], parameters['history_period'][0], 
                                  parameters['control_type'][0], parameters['id'][0])

        self._reload()
        self._done()
    
    @inlineCallbacks
    def delete(self, obj):
        yield self.db.del_value(int(obj.id))
        self._objects.remove(obj)
        obj.request.finish()
    
    def getChild(self, name, request):
               
        try:
            action = request.args['action'][0]
        except KeyError:
            action = None
        
        if not action:
            
            for obj in self._objects:
                if name == str(obj.id):
                    return obj
                
            return NoResource(message="The resource %s was not found" % request.URLPath())
        
        else:

            for obj in self._objects: 
                if name == str(obj.id): 
                    device_address = obj.device_address 
                    plugin_id = obj.plugin_id
                    
                    if action == 'poweron' or action == 'poweroff' or action == 'fire':   
                        return ValueActionResult(plugin_id, device_address, obj.name, self.coordinator, action, {})
                    elif action == 'dim':
                        params = {'level': request.args['level'][0]}
                        return ValueActionResult(plugin_id, device_address, obj.name, self.coordinator, action, params)
                    elif action == 'thermostat_setpoint':
                        params = {'temp': request.args['temp'][0]}
                        return ValueActionResult(plugin_id, device_address, obj.name, self.coordinator, action, params)
        
class Values_view(Resource):
    
    def render_GET(self, request):
        lookup = TemplateLookup(directories=[houseagent.template_dir])
        template = lookup.get_template('values.html')
        return str(template.render())

class HistoryType(Resource):
    '''
    This object represents a HistoryType.
    '''
    def __init__(self, id, name):
        Resource.__init__(self)
        self.id = id
        self.name = name
        
    def json(self):
        return {'id': self.id, 'name': self.name}
    
    def render_GET(self, request):
        return json.dumps(self.json())

class HistoryTypes(HouseAgentREST):
    
    @inlineCallbacks            
    def _load(self):
        '''
        Load history types from the database.
        '''
        self._objects = []
        history_type_query = yield self.db.query_history_types()
        
        for history_type in history_type_query:
            hist = HistoryType(history_type[0], history_type[1])
            self._objects.append(hist)    

class HistoryPeriod(Resource):
    '''
    This object represents a HistoryPeriod.
    '''
    def __init__(self, id, name, secs, sysflag):
        Resource.__init__(self)
        self.id = id
        self.name = name
        self.secs = secs
        self.sysflag = sysflag

    def json(self):
        return {"id": self.id, "name": self.name,
                "secs": self.secs, "sysflag": self.sysflag}

    def render_GET(self, request):
        return json.dumps(self.json())

class HistoryPeriods(HouseAgentREST):

    @inlineCallbacks
    def _load(self):
        '''
        Load history periods from the database.
        '''
        self._objects = []
        history_period_query = yield self.db.query_history_periods()

        for period in history_period_query:
            hist = HistoryPeriod(period[0], period[1], period[2], period[3])
            self._objects.append(hist)

class ControlType(Resource):
    '''
    This object represents a ControlType.
    '''
    def __init__(self, id, name):
        Resource.__init__(self)
        self.id = id
        self.name = name

    def json(self):
        return {"id": self.id, "name": self.name}

    def render_GET(self, request):
        return json.dumps(self.json())

class ControlTypes(HouseAgentREST):
    
    @inlineCallbacks
    def _load(self):
        '''
        Load control types from the database.
        '''
        self._objects = []
        control_type_query = yield self.db.query_controltypes()
        
        for control_type in control_type_query:
            hist = ControlType(control_type[0], control_type[1])
            self._objects.append(hist)

class Event_create(Resource):
    """
    Template that creates a new event.
    """ 
    def __init__(self, database):
        Resource.__init__(self)
        self.db = database 
    
    @inlineCallbacks  
    def finished(self, result):
        lookup = TemplateLookup(directories=[houseagent.template_dir])
        template = lookup.get_template('event_create.html')
        
        triggertypes = yield self.db.query_triggertypes()
        devs = yield self.db.query_devices_simple()
        conditiontypes = yield self.db.query_conditiontypes()
        
        self.request.write(str(template.render(trigger_types=triggertypes, devices=devs, action_types=result,
                                               condition_types=conditiontypes))) 
        self.request.finish()            
    
    def render_GET(self, request):
        self.request = request

        self.db.query_actiontypes().addCallback(self.finished)
        
        return NOT_DONE_YET

class Event_value_by_id(Resource):
    """
    Get's current values by device id from the database and returns a JSON dataset.
    """
    def __init__(self, database):
        Resource.__init__(self)
        self.db = database 
    
    def jsonResult(self, results):
        output = dict()
        for result in results:
            output[result[0]] = result[1]
        
        self.request.write(str(json.dumps(output)))
        self.request.finish()        
    
    def render_GET(self, request):
        self.request = request
        deviceid = request.args["deviceid"][0]
        self.db.query_values_by_device_id(deviceid).addCallback(self.jsonResult)
        return NOT_DONE_YET 

class Event_actions_by_id(Resource):
    '''
    Get's possible actions for a value id.
    '''
    def __init__(self, database):
        Resource.__init__(self)
        self.db = database 
    
    def result(self, device_type):
        
        output = {}
        if device_type[0][1] == "CONTROL_TYPE_THERMOSTAT":
            output[0] = "Set thermostat setpoint"
        elif device_type[0][0] == "CONTROL_TYPE_DIMMER":
            output[0] = "Set dim level"
        elif device_type[0][0] == "CONTROL_TYPE_ON_OFF":
            output[1] = "Power on"
            output[0] = "Power off"
        else:
            output[0] = "No actions available for this device"
        
        self.request.write(str(json.dumps(output)))
        self.request.finish()
    
    def render_GET(self, request):
        self.request = request
        device_id = request.args["deviceid"][0]
        #db.query_device_type_by_device_id(device_id).addCallback(self.result)
        self.db.query_action_types_by_device_id(device_id).addCallback(self.result)
        return NOT_DONE_YET
    
class Event_control_types_by_id(Resource):
    def __init__(self, database):
        Resource.__init__(self)
        self.db = database 
    
    def result(self, action_type):
        
        output = {}
        if action_type[0][0] == "CONTROL_TYPE_THERMOSTAT":
            output[0] = "Set thermostat setpoint"
        elif action_type[0][0] == "CONTROL_TYPE_DIMMER":
            output[0] = "Set dim level"
        elif action_type[0][0] == "CONTROL_TYPE_ON_OFF":
            output[1] = "Power on"
            output[0] = "Power off"
        else:
            output[0] = "No actions available for this device"
        
        self.request.write(str(json.dumps(output)))
        self.request.finish()

    def render_GET(self, request):
        self.request = request
        value_id = request.args["valueid"][0]
        self.db.query_action_type_by_value_id(value_id).addCallback(self.result)
        return NOT_DONE_YET    
    
class Event_control_values_by_id(Resource):
    def __init__(self, database):
        Resource.__init__(self)
        self.db = database 
    
    def result(self, results):
        output = dict()
        for result in results:
            output[result[0]] = result[1]
        
        self.request.write(str(json.dumps(output)))
        self.request.finish() 

    def render_GET(self, request):
        self.request = request
        device_id = request.args["deviceid"][0]
        self.db.query_action_types_by_device_id(device_id).addCallback(self.result)
        return NOT_DONE_YET              

class Event_getvalue(Resource):
    """
    Get's a value's current value by value id (no I'm not drunk at the moment :-) )
    """
    def __init__(self, database):
        Resource.__init__(self)
        self.db = database 
        
    def valueResult(self, result):
        self.request.write(str(result[0][0]))
        self.request.finish()
    
    def render_GET(self, request):
        self.request = request
        valueid = request.args['valueid'][0]
        self.db.query_value_by_valueid(valueid).addCallback(self.valueResult)
        return NOT_DONE_YET
 
class Event_save(Resource):
    """
    Save's event to the database.
    """
    def __init__(self, eventengine, database):
        Resource.__init__(self)
        self.eventengine = eventengine    
        self.db = database
    
    def finished(self, result):
        self.eventengine.reload()
        self.request.write(str(result))
        self.request.finish()
    
    def render_POST(self, request):
       
        self.request = request
        event_info = json.loads(request.content.read())
            
        if event_info['enabled'] == "yes": 
            enabled = True
        else:
            enabled = False
            
        print "event_info", event_info

        self.db.add_event2(event_info["name"], enabled, event_info["conditions"], event_info["actions"], event_info["trigger"]).addCallback(self.finished)
        
        return NOT_DONE_YET


class GraphValue(Resource):
    '''
    This object represents a Location.
    '''
    def __init__(self, value, ts):
        Resource.__init__(self)
        self.value = value
        self.ts = (ts * 1000) # due to the JS
        
    def json(self):
        return [self.ts, self.value]
    
    def render_GET(self, request):
        return json.dumps(self.json())


class GraphLatest(HouseAgentREST):
    '''
    This class implements a basic REST interface.
    '''
    def __init__(self, db):
        Resource.__init__(self)
        self.db = db
        self._objects = []

    def render_GET(self, request):
        self._objects = []

        self.request = request

        val_id = request.args["val_id"][0]
        #type = request.args["type"][0]
        ##period = request.args["period"][0]
        
        self._load(val_id).addCallback(self.done)

        return NOT_DONE_YET
    
    def done(self, result):
    
        output = []
        for obj in self._objects:
            output.append(obj.json())

        self.request.write(json.dumps(output))
        self.request.finish()
    
    @inlineCallbacks            
    def _load(self, params):
        '''
        Load plugins from the database.
        '''
        self.histview = HistoryViewer(self.db)
        self._objects = []
        value_query = yield self.histview.get_latest_data(params)
        
        for value in value_query:
            val = GraphValue(float(value[0]), float(value[1]))
            self._objects.append(val)


class GraphDaily(HouseAgentREST):
    '''
    This class implements a basic REST interface.
    '''
    def __init__(self, db):
        Resource.__init__(self)
        self.db = db
        self._objects = []

    def render_GET(self, request):
        self._objects = []

        self.request = request

        val_id = request.args["val_id"][0]
        #type = request.args["type"][0]
        ##period = request.args["period"][0]
        
        self._load(val_id).addCallback(self.done)

        return NOT_DONE_YET
    
    def done(self, result):
    
        output = []
        val = []
        min = []
        avg = []
        max = []

        for obj in self._objects:
            val.append(obj["val"].json())
            min.append(obj["min"].json())
            avg.append(obj["avg"].json())
            max.append(obj["max"].json())
        
        #for obj in self._objects:
        #    output.append(obj.json())
        output.append(val)
        output.append(min)
        output.append(avg)
        output.append(max)

        self.request.write(json.dumps(output))
        self.request.finish()
    
    @inlineCallbacks            
    def _load(self, params):
        '''
        Load plugins from the database.
        '''
        self.histview = HistoryViewer(self.db)
        self._objects = []
        value_query = yield self.histview.get_daily_data(params)
        
        for value in value_query:
            val = GraphValue(float(value[0]), float(value[4]))
            min = GraphValue(float(value[1]), float(value[4]))
            avg = GraphValue(float(value[2]), float(value[4]))
            max = GraphValue(float(value[3]), float(value[4]))
            _tmp = {"val": val, "min": min, "avg": avg, "max": max}
            self._objects.append(_tmp)


class CreateGraph(Resource):
    """
    Template for creating a graph.
    """
    def __init__(self, database):
        Resource.__init__(self)
        self.db = database
        self._types = {}

        reactor.callLater(0, self._load_history_types)


    @inlineCallbacks
    def _load_history_types(self):
        types = yield self.db.query_history_types()

        for type in types:
            self._types[type[0]] = {"type": type[1]}


    def _filter_disabled(self, data):
        _data = []
        for i in data:
            _val = ()
            # only items w/ enabled collecting
            if i[2] > 1:
                _resolved_type = self._types[i[3]]["type"].lower()
                _val = (i[0], i[1], _resolved_type)
                _data.append(_val)

        return _data


    def result(self, result):
        lookup = TemplateLookup(directories=[houseagent.template_dir])
        template = lookup.get_template('graph_create.html')

        filtered = self._filter_disabled(result)

        self.request.write(str(template.render(result=filtered)))
        self.request.finish()
    
    def render_GET(self, request):
        self.request = request
        self.db.query_values_light().addCallback(self.result)
        return NOT_DONE_YET
    

class Control(Resource):
    """
    Class that manages device control.
    """
    def __init__(self, database):
        Resource.__init__(self)
        self.db = database 
    
    def valueProcessor(self, result):
        lookup = TemplateLookup(directories=[houseagent.template_dir])
        template = lookup.get_template('control.html')
        
        self.request.write(str(template.render(result=result))) 
        self.request.finish()          
    
    def render_GET(self, request):
        self.request = request
        self.db.query_controllable_values().addCallback(self.valueProcessor)
        return NOT_DONE_YET
    
class Event(object):
    '''
    Skeleton class for event information.
    '''
    def __init__(self, id, name, enabled):
        self.id = id
        self.name = name
        self.enabled = enabled
        
    def __str__(self):
        return 'id: [{0}] name: [{1}] enabled: {2}'.format(self.id, self.name, self.enabled)

class Events(Resource):
    '''
    Class that shows all the events in the database, and that allows event management.
    '''
    def __init__(self, database):
        Resource.__init__(self)
        self.db = database 

    @inlineCallbacks
    def result(self, result):
        # Reuse skeleton classes from event engine
        from houseagent.core.events import Trigger, Condition, Action         
        events = []
        triggers = []
        conditions_out = []
        actions = []
        
        for event in result:
            e = Event(event[0], event[1], bool(event[2]))
            events.append(e)
        
        trigger_query = yield self.db.query_triggers()

        for trigger in trigger_query:   
            t = Trigger(trigger[1], trigger[2], trigger[3])
            
            # get trigger parameters
            trigger_parameters = yield self.db.query_trigger_parameters(trigger[0])
            
            for param in trigger_parameters:
                if param[0] == "cron":
                    
                    days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
                    cron = param[1].split(' ')
                    cron_text = 'Triggered every {0} at {1}:{2}'.format(
                        ','.join(days[int(n)] for n in cron[4].split(',')),
                        cron[1], cron[0])
                    
                    t.cron = cron_text
                elif param[0] == "current_value_id":
                    t.current_value_id = param[1]
                elif param[0] == "condition":
                    conditions = {"eq" : "is equal to",
                                  "ne" : "is not equal to",
                                  "lt" : "is less then",
                                  "gt" : "is greater then"}
                    
                    t.condition = conditions[param[1]]
                elif param[0] == "condition_value":
                    t.condition_value = param[1]
                    
            if t.type == "Device value change":
                extra = yield self.db.query_extra_valueinfo(t.current_value_id)
                
                t.device = extra[0][0]
                t.value = extra[0][1]
                                    
            triggers.append(t)
            
        condition_query = yield self.db.query_conditions()
        
        for condition in condition_query:
            c = Condition(condition[1], condition[2])
            
            condition_parameters = yield self.db.query_condition_parameters(condition[0])
            
            for param in condition_parameters:
                if param[0] == "condition":
                    conditions = {"eq" : "must be equal to",
                                  "ne" : "must not be equal to",
                                  "lt" : "must be less then",
                                  "gt" : "must be greater then"}                    
                    c.condition = conditions[param[1]]
                elif param[0] == "condition_value":
                    c.condition_value = param[1]
                elif param[0] == "current_values_id":
                    c.current_values_id = param[1]

            if c.type == "Device value":
                extra = yield self.db.query_extra_valueinfo(c.current_values_id)
                
                c.device = extra[0][0]
                c.value = extra[0][1]                

            conditions_out.append(c)  
            
        actions_query = yield self.db.query_actions()
        print "actions: " + str(actions_query)

        for action in actions_query:
            a = Action(action[1], action[2])
            
            action_parameters = yield self.db.query_action_parameters(action[0])
            for param in action_parameters:
                if param[0] == "device":
                    device = yield self.db.query_device(param[1])
                    a.device = device[0][1]
                elif param[0] == "control_value":
                    extra = yield self.db.query_extra_valueinfo(param[1])
                    a.control_value = param[1]
                    a.control_value_name = extra[0][1]
                elif param[0] == "command":
                    if param[1] == "1": a.command = "on"
                    elif param[1] == "0": a.command = "off"
                    else:
                        a.command = param[1]
                
            if action[1] == "Device action":               
                # fetch control_type
                control_type = yield self.db.query_controltypename(a.control_value)
                print "Control type" + str(control_type)
                a.control_type = control_type[0][0]
            
            actions.append(a)      

        lookup = TemplateLookup(directories=[houseagent.template_dir])
        template = lookup.get_template('events.html')
        
        self.request.write(str(template.render(events=events, triggers=triggers, conditions=conditions_out,
                                               actions=actions)))
        self.request.finish()
    
    def render_GET(self, request):
        self.request = request
        self.db.query_events().addCallback(self.result)
        return NOT_DONE_YET
    
class Event_del(Resource):
    '''
    Class that handles deletion of events from the database.
    '''
    def __init__(self, eventengine, database):
        Resource.__init__(self)
        self.eventengine = eventengine
        self.db = database
    
    def event_deleted(self, result):
        self.eventengine.reload()
        self.request.write(str("done!"))
        self.request.finish()
    
    def render_POST(self, request):
        self.request = request              
        id = request.args["id"][0]
        
        self.db.del_event(int(id)).addCallback(self.event_deleted)
        return NOT_DONE_YET
