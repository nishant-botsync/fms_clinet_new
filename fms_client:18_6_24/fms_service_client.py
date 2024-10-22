#!/usr/bin/python

import datetime
from autobahn.twisted.websocket import WebSocketClientProtocol
from twisted.internet.protocol import ReconnectingClientFactory
from autobahn.twisted.websocket import WebSocketClientFactory
from autobahn.twisted.websocket import WebSocketClientFactory, WebSocketClientProtocol, connectWS
from twisted.python import log
from twisted.internet import reactor

from base64 import standard_b64encode, standard_b64decode

import rospy
import random
import os
from std_msgs.msg import String, Bool
from rospy_message_converter import json_message_converter, message_converter
# import message_filters
from rosbridge_library.internal.services import call_service

from rostopic import get_topic_type
from rosservice import get_service_type

import json
import sys
import time

from autobahn.websocket.compress import PerMessageDeflateOffer, \
    PerMessageDeflateResponse, \
    PerMessageDeflateResponseAccept

from threading import Lock, Thread , Event
# import websocket, rel
# import websocket
try:
    import thread
except ImportError:
    import _thread as thread
import time
import re
from twisted.internet import reactor, ssl

import roslib
_manifest_lock = Lock()
_subscriber_list_lock = Lock()

robot_name = ""
client_ip = ""
ros_client = None
ws_client = None

class ThreadManager:
    def __init__(self):
        self.threads = {}

    def start_thread(self, name, target, args=()):
        stop_event = Event()  # Create a stop event for the thread
        # In Python 2, you can't unpack like *args, so pass stop_event manually
        thread = Thread(target=target, args=(stop_event,) + args)
        thread.start()
        self.threads[name] = (thread, stop_event)

    def stop_thread(self, name):
        if name in self.threads:
            thread, stop_event = self.threads[name]
            stop_event.set()  # Signal the thread to stop
            thread.join()  # Wait for the thread to finish
            del self.threads[name]
        else:
            # Use traditional string formatting in Python 2
            print("No thread with name %s found" % name)


def logger_error_configuration(*args,**kwargs):
    import inspect
    import os
    frame = inspect.currentframe().f_back
    file_name = os.path.basename(__file__)
    line_number = frame.f_lineno 
    log_message = " ".join(map(str, args))
    
    if kwargs:
        kwarg_message = " ".join("{}={}".format(k, v) for k, v in kwargs.items())
        log_message += " " + kwarg_message
    
    complete_message = "{}:{} {}".format(file_name,line_number,log_message)

    rospy.logerr(complete_message)
    
def logger_info_configuration(*args,**kwargs):
    import inspect
    import os
    frame = inspect.currentframe().f_back
    file_name = os.path.basename(__file__)
    line_number = frame.f_lineno 
    log_message = " ".join(map(str, args))
    
    if kwargs:
        kwarg_message = " ".join("{}={}".format(k, v) for k, v in kwargs.items())
        log_message += " " + kwarg_message
        
    complete_message = "{}:{} {}".format(file_name,line_number,log_message)

    rospy.loginfo(complete_message)

def _load_class(modname, subname, classname):
    """ Loads the manifest and imports the module that contains the specified
    type.
    Logic is similar to that of roslib.message.get_message_class, but we want
    more expressive exceptions.
    Returns the loaded module, or None on failure """
    global loaded_modules

    try:
        with _manifest_lock:
            # roslib maintains a cache of loaded manifests, so no need to duplicate
            roslib.launcher.load_manifest(modname)
    except Exception as exc:
        raise Exception(modname, exc)

    try:
        pypkg = __import__('%s.%s' % (modname, subname))
    except Exception as exc:
        raise Exception(modname, subname, exc)

    try:
        return getattr(getattr(pypkg, subname), classname)
    except Exception as exc:
        raise Exception(modname, subname, classname, exc)



def convert(input):
    if isinstance(input, dict):
        return {convert(key): convert(value) for key, value in input.items()}
    elif isinstance(input, list):
        return [convert(element) for element in input]
    elif isinstance(input, type(u'')):
        return input.encode('utf-8')
    else:
        return input


class RosClient():

    list_braces = re.compile(r'\[[^\]]*\]')
    ros_binary_types_list_braces = [("uint8[]", re.compile(r'uint8\[[^\]]*\]')),
                                ("char[]", re.compile(r'char\[[^\]]*\]'))]
    ros_binary_types = ["uint8[]", "char[]"]
    ros_time_types = ["time", "duration"]
    ros_primitive_types = ["bool", "byte", "char", "int8", "uint8", "int16",
                       "uint16", "int32", "uint32", "int64", "uint64",
                       "float32", "float64", "string"]

    type_map = {
    "bool":    ["bool"],
    "int":     ["int8", "byte", "uint8", "char",
                "int16", "uint16", "int32", "uint32",
                "int64", "uint64", "float32", "float64"],
    "float":   ["float32", "float64"],
    "str":     ["string"],
    "unicode": ["string"],
    "long":    ["int64", "uint64"]
    }
    primitive_types = [bool, int, float]
    list_types = [list, tuple]
    
    service_calls_stack = {}
    tm = ThreadManager()
    

    def __init__(self, name):
        self.robot_pos_prev = time.time()
        now = time.time()
        self.robot_name = name
        self.last_msg = {
            "/"+self.robot_name+"/topmodule_robot_fms_ping": now
        }
        self.subscriber_list = {}
        self.pub = {}
        self.fms_pub = rospy.Publisher("fms_connection_status",Bool,queue_size=1)
        rospy.Timer(rospy.Duration(3),self.ping_fms)
        # ws_client = protocol

    def update_client(self, protocol):
        self.ws_client = protocol
    
    def __str__(self):
        return self.robot_name
    
    def ping_fms(self,event):
        try:
            if client_ip:
                cmd="ping -c 1 -W1 "+client_ip+"> /dev/null 2>&1"
                response = os.system(cmd)
                msg = Bool()
                if response == 0:
                    msg.data = True
                else:
                    msg.data = False
                self.fms_pub.publish(msg)
        except Exception as e:
            logger_info_configuration("Error in ping fms ",str(e))

    def send_message(self, msg, *args):
        try:
            topic_name = args[0]

            now = time.time()
            
            
            if (topic_name in self.last_msg.keys() and now - self.last_msg.get(topic_name) < 0.2):
                return
            elif (topic_name in self.last_msg.keys()):
                self.last_msg[topic_name] = now
            
            # "datetime": str(that_time),
            msg = {
                "op": "SUB_MSGS",
                "topic": args[0],
                "robot_name": self.robot_name,
                "msg": json_message_converter.convert_ros_message_to_json(msg)
            }
            
            reactor.callFromThread(self.ws_client.callback, json.dumps(msg, ensure_ascii=False).encode('utf8'))
        except Exception as e:
            logger_info_configuration("Exception while sending message from subscriber: ",str(e))
    
    def publish(self, topic, message, date):
        #if not self.pub.get(topic):
        try:
            topic_type = get_topic_type(topic)[0]
            
            if not topic_type:
                raise ValueError("{} is not there".format(topic))
            
            msg_class_ = _load_class(topic_type.partition("/")[0], "msg", topic_type.partition("/")[2])
            #message = convert(message)
            logger_info_configuration(topic_type)
            logger_info_configuration(msg_class_._type)
            if (msg_class_._type == topic_type):
                pub = self.pub.get(topic)
                if not pub:
                    pub = rospy.Publisher(topic, msg_class_, queue_size=2)
                    self.pub[topic] = pub
                count = 0
                while pub.get_num_connections() == 0:
                    if count > 5:
                        break
                    logger_info_configuration("registering....")
                    rospy.sleep(1)
                    count += 1
                msg = message_converter.convert_dictionary_to_ros_message(topic_type, message)
                logger_info_configuration(msg)
                pub.publish(msg)
                logger_info_configuration("Published")
        except Exception as e:
            logger_info_configuration("Exception while publishing message: ",str(e))    
            return None
            
    def service_call(self, service, args,service_id):
        try:
            #args = convert(args)
            resp = call_service(service, args)
            logger_info_configuration(resp)
            msg = {
                    "op": "SERVICE_MSG",
                    "service": service,
                    "result": resp,
                    "ID": service_id
                    }
            logger_info_configuration("sending to root")
            print('service call stored in variable')
            self.service_calls_stack[service_id] = msg
            print('sending to fms the response')
            self.ws_client.callback(json.dumps(msg, ensure_ascii=False).encode('utf8'))
        except Exception as e:
            logger_info_configuration("Exception while calling service: ",str(e))
            
    def run_command(self, message):
            m = json.loads(message)
            op = m.get("OP")
            logger_info_configuration(message)
            if op == "PUBLISH":
                logger_info_configuration("Publish received")
                topic = m.get("topic")
                message = m.get("data")
                date = m.get("date")
                if not date:
                    logger_info_configuration("Send date not found")
                    date = datetime.datetime.utcnow()
                    self.publish(topic, message, date)
                    # publish_thread.start()
                    return None
                # logger_info_configuration(date)
                date_now = datetime.datetime.utcnow()
                date = datetime.datetime.utcfromtimestamp(date)
                stop_time = 3.0
                if topic == "/" + str(self.robot_name) + "/website/cmd_vel":
                    stop_time = random.random() * 3.0
                logger_info_configuration(stop_time)
                logger_info_configuration((date_now - date).total_seconds())
                if (date_now - date).total_seconds() > stop_time:
                    logger_info_configuration("Date is too old")
                    return None
                self.publish(topic, message, date)
                # self.publish(topic, message)
            if op == "SERVICE":
                service_id =  m.get('ID')
                print('$%^Y&U service stack {} @#$%^&'.format(self.service_calls_stack))
                print('recieved service call')
                if self.service_calls_stack.get(service_id):
                    logger_info_configuration("Duplicate service, sending stored response")
                    self.ws_client.callback(json.dumps(self.service_calls_stack[service_id], ensure_ascii=False).encode('utf8'))
                    return
                    
                   
                logger_info_configuration("Service received")
                
                # sending ack back
                print('was not in stored variables, sending ack back for a fresh start')
                msg = {
                    "ID": service_id,
                    "op": "ACK_FROM_ROBOT"
                }
                self.ws_client.callback(json.dumps(msg, ensure_ascii=False).encode('utf8'))

                
                logger_info_configuration("sending to root")
                service = m.get("service")
                args = m.get("data")

                date = m.get("date")
                if not date:
                    logger_info_configuration("Send date not found")
                    self.service_call(service, args,service_id)
                    return None
                date_now = datetime.datetime.utcnow()
                date = datetime.datetime.utcfromtimestamp(date)
                if (date_now - date).total_seconds() > 3.0:
                    logger_info_configuration("Date is too old")
                    return None
                self.service_call(service, args,service_id)
            if op == "ACK_FROM_FMS":
                service_id =  m.get('ID')
                if self.service_calls_stack.get(service_id):
                    print('fms recieved the call , not poping the stack')
                    del self.service_calls_stack[service_id]
                    return None

class MyClientProtocol(WebSocketClientProtocol):


    def update_robot_version(self):
        version = os.environ.get("ROBOT_VER")
        version = os.getenv('ROBOT_VER')
        msg = {
            "op": "VERSION_UPDATE",
            "robot_name": robot_name,
            "version": version
        }
        self.sendMessage(json.dumps(msg).encode('utf8'))
    
    def __init__(self):
        
        super(MyClientProtocol, self).__init__()
        ros_client.update_client(self)
    
    
    def onOpen(self):        
        logger_info_configuration("OnOpen!")
        thread = {}

    def callback(self, data):
        self.sendMessage(data)
      
    def onConnect(self, request):
        #return None
        logger_info_configuration("Connected!")
        self.factory.resetDelay()

    def onMessage(self, payload, isBinary):
        
        print ("#####  onMessage #######")
        if isBinary:
            logger_info_configuration("Received")
        else:
            logger_info_configuration("Received")
            if ros_client:
                ros_client.run_command(payload.decode('utf8'))
	        

    def onClose(self, wasClean, code, reason):

        print ("########## onClose ###############")
        logger_info_configuration("was_clean: {}".format(wasClean))
        logger_info_configuration("Close status code {}".format(code))
        logger_info_configuration("Reason: {}".format(reason))


class MyClientFactory(WebSocketClientFactory, ReconnectingClientFactory):

    maxDelay = 5.0

    def clientConnectionFailed(self, connector, reason):
        logger_info_configuration("Client connection failed .. retrying ..")
        self.retry(connector)

    def clientConnectionLost(self, connector, reason):
        logger_info_configuration("Client connection lost .. retrying ..")
        self.retry(connector)


'''
if __name__=="__main__":
    rospy.init_node("FMS_client", anonymous=True)

    r = RosClient("mag300_005", "172.18.10.142")
    r.start()

    rospy.spin()
'''

if __name__ == '__main__':

    import sys
    log.startLogging(sys.stdout)

    rospy.init_node('listener', anonymous=True)
    robot_name = sys.argv[1]
    client_ip = sys.argv[2]
    ros_client = RosClient(robot_name)
    factory = MyClientFactory("wss://" + client_ip +":8000/ws/robot_service_pub/"+ robot_name + "/")
    factory.protocol = MyClientProtocol
    factory.setProtocolOptions(
        autoPingInterval=10,
        autoPingTimeout=5
    )

    offers = [PerMessageDeflateOffer()]

    def accept(response):
        if isinstance(response, PerMessageDeflateResponse):
            return PerMessageDeflateResponseAccept(response)

    if factory.isSecure:
        contextFactory = ssl.ClientContextFactory()
    else:
        contextFactory = None

    connectWS(factory, contextFactory)


    reactor.run()

    
