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

from threading import Lock, Thread
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
        return {convert(key): convert(value) for key, value in input.iteritems()}
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

    def __init__(self, name):
        self.robot_pos_prev = time.time()
        self.task_manager_topics = [
            "TMS_status",
            "topmodule_robot_fms",
            "topmodule_robot_fms_ping"
            ]
        now = time.time()
        self.robot_name = name
        self.last_msg = {
            "/"+self.robot_name+"/TMS_status": now,
            "/"+self.robot_name+"/topmodule_robot_fms": now,
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
            logger_error_configuration("Error in ping fms ",str(e))

    def send_message(self, msg, *args):
        try:
            # logger_info_configuration(args)
            topic_name = convert(args[0])
            # robot_pose = topic_name == "/"+self.robot_name+"/robot_pose"
            # if robot_pose and (time.time() - self.last_msg["/"+self.robot_name+"/robot_pose"]) < 0.2:
            #         return
            
            #if (time.time() - self.now > 1.0):
                #for k in self.last_msg.keys():
                #    self.last_msg[k] = 0
                #self.now = time.time()

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
            # logger_info_configuration(msg)
            #logger_info_configuration("Callback called")
            
            print("tms message sending to fms",msg)
            
            reactor.callFromThread(self.ws_client.callback, json.dumps(msg, ensure_ascii=False).encode('utf8'))
            # rospy.sleep(0.1)
            
            # self.ws_client.callback(json.dumps(msg))
            # time.sleep(0.1)
            ## send to nodejs serve
        except Exception as e:
            logger_error_configuration("Exception while sending message from subscriber: ",str(e))
            # logger_info_configuration(e) 

    def subscribe_callback(self, msg, *args):
        # logger_info_configuration(args)
        self.send_message(msg, args[0])

    def init_subscription(self):
        for j in self.task_manager_topics:
            # function_this = lambda msg: self.subscribe_callback(msg, "/" + self.robot_name + "/" + j)
            self.subscribe_topics("/" + self.robot_name + "/" + j)
            # self.subscribe_topics("/" + self.robot_name + "/" + "robot_pose", lambda msg: self.subscribe_callback(msg, "/" + self.robot_name + "/" + "robot_pose"))

    # def unsubscribe_all(self):
    #     for k in self.subscriber_list.keys():
    #         self.subscriber_list[k].unregister()
    #         del self.subscriber_list[k]

    def unsubscribe_topics(self, topic):
        try:
            if self.subscriber_list.get(topic):
                self.subscriber_list[topic].unregister()
                del self.subscriber_list[topic]
        except Exception as e:
            logger_error_configuration("Exception while unsubscribing topic: ",str(e))
            # raise Exception("Unregister failed for %s" % topic)

    # def subscribe(self, topic, callback):
    #     topic_type = get_topic_type(topic)[0]
    
    
    def publish(self, topic, message, date):
        #if not self.pub.get(topic):
        try:
            topic_type = get_topic_type(topic)[0]
            
            if not topic_type:
                raise ValueError("{} is not there".format(topic))
            
            msg_class_ = _load_class(topic_type.partition("/")[0], "msg", topic_type.partition("/")[2])
            message = convert(message)
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
                #pub = self.pub[topic]
                #message = convert(message)
                #topic_type = get_topic_type(topic)[0]
                # date_now = datetime.datetime.utcnow()
                # logger_info_configuration(date)
                # logger_info_configuration(date_now)
                # logger_info_configuration((date_now - date).total_seconds())
                # if topic_type == "geometry_msgs/Twist":
                #     timeout = 2
                # else:
                #     timeout = 3
                # logger_info_configuration(timeout)
                # if (date_now - date).total_seconds() > timeout:
                #     logger_info_configuration("Date is too old")
                #     # pub.unregister()
                #     return None
                msg = message_converter.convert_dictionary_to_ros_message(topic_type, message)
                logger_info_configuration(msg)
                pub.publish(msg)
                logger_info_configuration("Published")
        except Exception as e:
            logger_error_configuration("Exception while publishing message: ",str(e)) 
            return None    
            # pub.unregister()

    # def _to_binary_inst(self, msg):
    #     if type(msg) == str:
    #         try:
    #             return standard_b64decode(msg)
    #         except :
    #             return msg
    #     else:
    #         try:
    #             return bytes(bytearray(msg))
    #         except:
    #             return msg

    
    # def _to_time_inst(self, msg, rostype, inst=None):
    #     if rostype == "time" and msg == "now":
    #         return rospy.get_rostime()

    #     if inst is None:
    #         if rostype == "time":
    #             inst = rospy.rostime.Time()
    #         elif rostype == "duration":
    #             inst = rospy.rostime.Duration()
    #         else:
    #             return None
        
    #     for field in ["secs", "nsecs"]:
    #         try:
    #             if field in msg:
    #                 setattr(inst, field, msg[field])
    #         except TypeError:
    #             continue

    #     return inst

    # def _to_primitive_inst(self, msg, rostype, roottype, stack):
    #     # Typecheck the msg
    #     msgtype = type(msg)
    #     if msgtype in self.primitive_types and rostype in self.type_map[msgtype.__name__]:
    #         return msg
    #     elif msgtype in ["string"] and rostype in self.type_map[msgtype.__name__]:
    #         return msg.encode("utf-8", "ignore")
    #     raise Exception("Primitive data type --- datatype mismatch reported")
    

    # def _to_list_inst(self, msg, rostype, roottype, inst, stack):
    # # Typecheck the msg
    #     if type(msg) not in self.list_types:
    #         raise Exception("Expected a list data type")

    #     # Can duck out early if the list is empty
    #     if len(msg) == 0:
    #         return []

    #     # Remove the list indicators from the rostype
    #     rostype = self.list_braces.sub("", rostype)

    #     # Call to _to_inst for every element of the list
    #     return [self._to_inst(x, rostype, roottype, None, stack) for x in msg]

    # def _to_object_inst(self, msg, rostype, roottype, inst, stack):
    # # Typecheck the msg
    #     if type(msg) is not dict:
    #         raise Exception("Expected a dictionary as message")

    #     # Substitute the correct time if we're an std_msgs/Header
    #     try:
    #         if rostype in self.ros_header_types:
    #             cur_time = rospy.get_rostime()
    #             # copy attributes of global Time obj to inst.stamp
    #             inst.stamp.secs = cur_time.secs
    #             inst.stamp.nsecs = cur_time.nsecs
    #     except rospy.exceptions.ROSInitException as e:
    #         rospy.logdebug("Not substituting the correct header time: %s" % e)

    #     inst_fields = dict(zip(inst.__slots__, inst._slot_types))

    #     for field_name in msg:
    #         # Add this field to the field stack
    #         field_stack = stack + [field_name]

    #         # Raise an exception if the msg contains a bad field
    #         if not field_name in inst_fields:
    #             raise NonexistentFieldException(roottype, field_stack)

    #         field_rostype = inst_fields[field_name]
    #         field_inst = getattr(inst, field_name)

    #         field_value = self._to_inst(msg[field_name], field_rostype,
    #                     roottype, field_inst, field_stack)

    #         setattr(inst, field_name, field_value)

    #     return inst

    
    # def _to_inst(self, msg, rostype, roottype, inst=None, stack=[]):

    #     for binary_type, expression in self.ros_binary_types_list_braces:
    #         if expression.sub(binary_type, rostype) in self.ros_binary_types:
    #             return self._to_binary_inst(msg)
        
    #     if rostype in self.ros_time_types:
    #         return self._to_time_inst(msg, rostype, inst)
        
    #     if rostype in self.ros_primitive_types:
    #         return self._to_primitive_inst(msg, rostype, roottype, stack)
    
    #     if inst is not None and type(inst) in self.list_types:
    #         return self._to_list_inst(msg, rostype, roottype, inst, stack)
        
    #     if inst is None:
    #         inst = ros_loader.get_message_instance(rostype)
    
    #     return self._to_object_inst(msg, rostype, roottype, inst, stack)

    # def populate_instance(self, msg, inst):
    #     """
    #         Populate the provided instance, propagating any exceptions
    #     """

    #     return self._to_inst(msg, inst._type, inst._type, inst)

    
    
    
    
    
    # def args_to_service_request_instance(self, service, inst, args):
    #     """ Populate a service request instance with the provided args

    #     args can be a dictionary of values, or a list, or None

    #     Propagates any exceptions that may be raised. """
    #     msg = {}
    #     if isinstance(args, list):
    #         msg = dict(zip(inst.__slots__, args))
    #     elif isinstance(args, dict):
    #         msg = args

    #     # Populate the provided instance, propagating any exceptions
    #     self.populate_instance(msg, inst)


    def service_call(self, service, args):
        # rospy.wait_for_service(service)
        # logger_info_configuration(service)
        # service_type = get_service_type(service)
        # # service_class = get_service_class(service_type)
        # logger_info_configuration(service_type)
        # #Load class gives the service class 
        # msg_class_ = _load_class(service_type.partition("/")[0], "srv", service_type.partition("/")[2])
        # print (msg_class_)
        # inst = msg_class_._request_class()
        # #Convert args to Service request instance
        # self.args_to_service_request_instance(service, inst, args)
        # # if (msg_class_._type == topic_type) :
        # srv = rospy.ServiceProxy(service, msg_class_)
        # logger_info_configuration(srv)
        # resp = {}
        # logger_info_configuration(args)
        # if not args:
        #     resp = srv()
        # else:
        #     #resp = srv(message_converter.convert_dictionary_to_ros_message(service_type, args))
        #     # srv_args = args.values()
        #     # logger_info_configuration(type(srv_args))
        #     resp = srv(inst)
        # resp = convert(resp)
        try:
            args = convert(args)
            resp = call_service(service, args)
            logger_info_configuration(resp)          
            msg = {
                    "op": "SERVICE_MSG",
                    "service": service,
                    "result": resp
                    }
            logger_info_configuration("sending to root")
            # send to nodejs server
            # self.ws_client.sendMessage(json.dumps(msg, ensure_ascii = False).encode('utf8'), isBinary=False)
            self.ws_client.callback(json.dumps(msg, ensure_ascii=False).encode('utf8'))
        except Exception as e:
            logger_error_configuration("Exception while calling service: ",str(e))
            # logger_info_configuration(e) 
    # except Exception as e:
    #     logger_info_configuration(e)
    
    def subscribe_topics(self, topic):
        
        
        try:
            logger_info_configuration(topic)
            logger_info_configuration(self.subscriber_list.get(topic))
            if self.subscriber_list.get(topic):
                unsubscribe = Thread(
                    target=self.unsubscribe_topics,
                    args=(topic,))
                unsubscribe.start()
            topic_type = get_topic_type(topic)[0]
            logger_info_configuration(get_topic_type(topic))
            if (topic_type is None):
                raise Exception("Topic type unavaialble")
            else:
                msg_class_ = _load_class(topic_type.partition("/")[0], "msg", topic_type.partition("/")[2])
                if (msg_class_._type == topic_type) :
                    self.subscriber_list[topic] = rospy.Subscriber(
                        topic, 
                        msg_class_, 
                        self.subscribe_callback, 
                        callback_args=topic,
                        queue_size=1,
                        buff_size=1*2*1024
                        )
                else:
                    raise Exception("Topic type mismatch")
        except Exception as e:
            print (e)

    def run_command(self, message):
            m = json.loads(message)
            op = m.get("OP")
            # if op == "PUBLISH":
            #     logger_info_configuration("Publish received")
            #     topic = m.get("topic")
            #     message = m.get("data")
            #     date = m.get("date")
            #     if not date:
            #         logger_info_configuration("Send date not found")
            #         date = datetime.datetime.utcnow()
            #         self.publish(topic, message, date)
            #         # publish_thread.start()
            #         return None
            #     # logger_info_configuration(date)
            #     date_now = datetime.datetime.utcnow()
            #     date = datetime.datetime.utcfromtimestamp(date)
            #     # logger_info_configuration(date)
            #     # logger_info_configuration(date_now)
            #     # logger_info_configuration((date_now - date).total_seconds())
            #     stop_time = 3.0
            #     if topic == "/" + str(self.robot_name) + "/website/cmd_vel":
            #         stop_time = random.random() * 3.0
            #     logger_info_configuration(stop_time)
            #     logger_info_configuration((date_now - date).total_seconds())
            #     if (date_now - date).total_seconds() > stop_time:
            #         logger_info_configuration("Date is too old")
            #         return None
            #     #logger_info_configuration(message)
            #     self.publish(topic, message, date)
            #     # self.publish(topic, message)
            # if op == "SERVICE":
            #     logger_info_configuration("Service received")
            #     service = m.get("service")
            #     args = convert(m.get("data"))
            #     date = m.get("date")
            #     if not date:
            #         logger_info_configuration("Send date not found")
            #         self.service_call(service, args)
            #         # service_thread.start()
            #         return None
            #     # logger_info_configuration(date)
            #     date_now = datetime.datetime.utcnow()
            #     date = datetime.datetime.utcfromtimestamp(date)
            #     # logger_info_configuration(date)
            #     # logger_info_configuration(date_now)
            #     # logger_info_configuration((date_now - date).total_seconds())
            #     if (date_now - date).total_seconds() > 3.0:
            #         logger_info_configuration("Date is too old")
            #         return None
            #     self.service_call(service, args)

            if op == "SUBSCRIBE":
                topic = m.get("topic")
                logger_info_configuration(topic)
                self.subscribe_topics(topic)

            if op == "UNSUBSCRIBE":
                topic = m.get("topic")
                self.unsubscribe_topics(topic)

        # except Exception as e:
        #     logger_info_configuration(e)

    def task_state_callback(self, robot_state, bms, robot_pose, task_status, error_list, load_status):
        # task_state_callback
        logger_error_configuration ("running task state callback")
        data = {}
        data.update(message_converter.convert_ros_message_to_dictionary(robot_state))
        data.update(message_converter.convert_ros_message_to_dictionary(bms))
        data.update(message_converter.convert_ros_message_to_dictionary(robot_pose))
        data.update(message_converter.convert_ros_message_to_dictionary(task_status))
        data.update(message_converter.convert_ros_message_to_dictionary(error_list))
        data.update(message_converter.convert_ros_message_to_dictionary(load_status))

        logger_error_configuration (data)

        # when websocket is open push info

    def start(self):
        try :
            if not self.subscriber_list :
                with _subscriber_list_lock:
                    self.task_subscriber_list = []
                    for key in self.task_manager_topics:
                        topic_type = get_topic_type("/"+self.robot_name+"/"+key)[0]
                        if (topic_type is None ) :
                            raise Exception("Topic type unavaialble")
                        else :
                            msg_class_ = _load_class(topic_type.partition("/")[0], "msg", topic_type.partition("/")[2])
                            if (msg_class_._type == topic_type) :
                                self.subscriber_list[self.robot_name+"/"+key] = rospy.Subscriber("/"+self.robot_name+"/"+key, msg_class_, self.subscribe_callback, callback_args="/"+self.robot_name+"/"+key)
                                self.task_subscriber_list.append(self.subscriber_list[self.robot_name+"/"+key])
                            else:
                                raise Exception("Topic type mismatch")
                    print (self.task_subscriber_list)
                    # ts = message_filters.ApproximateTimeSynchronizer(self.task_subscriber_list, 1, 1, allow_headerless=True)
                    # ts.registerCallback(self.task_state_callback)
        except Exception as e:
            print (e)


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
        # logger_info_configuration(self.ros_client)
    
    
    def onOpen(self):        
        logger_info_configuration("OnOpen!")
        # Subscribes to initial topics on open connection
        # ws_client = self
        if ros_client:
            ros_client.init_subscription()
        self.update_robot_version()
        thread = {}

    def callback(self, data):
        # logger_info_configuration("### CALLBACK TRIGGERED!")
        # msg = {
        #     "op": "publish",
        #     "topic": topic,
        #     "msg": json_message_converter.convert_ros_message_to_json(data)
        # }
        # logger_info_configuration(msg)
        #logger_info_configuration(type(msg))
        # thread = Thread(target=self.sendMessage, args=(json.dumps(msg),))
        # thread.start()
        self.sendMessage(data)
      
    def onConnect(self, request):
        #return None
        logger_info_configuration("Connected!")
        if ros_client:
            ros_client.init_subscription()
        self.update_robot_version()
        self.factory.resetDelay()

    def onMessage(self, payload, isBinary):
        
        print ("#####  onMessage #######")
        if isBinary:
            # logger_info_configuration("Binary message received: {0} bytes".format(len(payload)))
            logger_info_configuration("Received")
        else:
            # logger_info_configuration("Text message received: {0}".format(payload.decode('utf8')))
            logger_info_configuration("Received")
            if ros_client:
                ros_client.run_command(payload.decode('utf8'))
	        

    def onClose(self, wasClean, code, reason):

        print ("########## onClose ###############")
        logger_info_configuration("was_clean: {}".format(wasClean))
        logger_info_configuration("Close status code {}".format(code))
        logger_info_configuration("Reason: {}".format(reason))


class MyClientFactory(WebSocketClientFactory, ReconnectingClientFactory):

    # protocol = MyClientProtocol
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

def on_message(ws, message):
        logger_info_configuration(message)
    
def on_error(ws, error):
    logger_info_configuration(error)

def on_close(ws):
    logger_info_configuration("### closed ###")

def on_open(ws):
    # def run(*args):
    #     for i in range(3):
    #         time.sleep(1)
    #         ws.send("Hello %d" % i)
    #     time.sleep(1)
    #     ws.close()
    #     logger_info_configuration("thread terminating...")
    # thread.start_new_thread(run, ())
    logger_info_configuration("connection is open")


# class WebSocketClient():

#     def __init__(self, robot_name, client_ip):
        
#         # super(MyClientProtocol, self).__init__()
#         self.robot_name = robot_name
#         self.client_ip = client_ip
#         self.ros_client = RosClient(self.robot_name, self)
#         logger_info_configuration(self.ros_client)
#         self.ws = None
#         # self.start(robot_name, client_ip)
#         # self.run_forever()
    
#     def start(self):
#         # websocket.enableTrace(True)
#         self.ws = websocket.WebSocketApp(
#             "ws://" + self.client_ip + ":7071/" + self.robot_name,
#             on_open=self.on_open,
#             on_message=self.on_message,
#             on_error=self.on_error,
#             on_close=self.on_close
#         )
#         self.ws.run_forever(skip_utf8_validation=True, ping_interval=10, ping_timeout=5,dispatcher=rel)
#         rel.signal(2, rel.abort)  # Keyboard Interrupt
#         rel.dispatch()
#     # def run_forever(self):
#     #     self.ws.run_forever()
    
#     def on_open(self, ws):
#         logger_info_configuration("Connect is open")
#         self.ros_client.init_subscription()
    
#     def callback(self, data):
#         # logger_info_configuration("### CALLBACK TRIGGERED!")
#         # logger_info_configuration(data)
#         # msg = {
#         #     "op": "publish",
#         #     "topic": topic,
#         #     "msg": json_message_converter.convert_ros_message_to_json(data)
#         # }
#         # logger_info_configuration(msg)
#         # #logger_info_configuration(type(msg))
#         if self.ws:
#             self.ws.send(data)
#             pass

#     def on_message(self, ws, message):
#         logger_info_configuration(message)
#         # if isBinary:
#         #     logger_info_configuration("Binary message received: {0} bytes".format(len(message)))
#         # else:
#         #     logger_info_configuration("Text message received: {0}".format(message.decode('utf8')))
#         self.ros_client.run_command(message)
    
#     def on_error(self, ws, error):
#         logger_info_configuration("error in connection")
#         logger_info_configuration(error)
    
#     def on_close(self, ws):
#         logger_info_configuration("Closed connection")




if __name__ == '__main__':

    import sys
    log.startLogging(sys.stdout)

    rospy.init_node('listener', anonymous=True)
    robot_name = sys.argv[1]
    client_ip = sys.argv[2]
    ros_client = RosClient(robot_name)
    # #ws::localhost
    # logger_info_configuration(client_ip)
    factory = MyClientFactory("wss://" + client_ip +":8001/ws/robot_tms_top/"+ robot_name + "/")
    # factory.protocol = MyClientProtocol(robot_name, robot_ip)
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
    #reactor.connectTCP("127.0.0.1", 9000, factory)
    #reactor.connectTCP("ws://172.18.10.201:8000/ws/robot_diagnostics/?token=2e056479df476b0809e93bd396f646d984ea5065a678f647f0bb98beee9c8c4a", 8000, factory, 20)

    #reactor.listenTCP(8000, factory)
    #reactor.connectTCP("ws://172.18.10.201", 8000, factory, 20)

   


    reactor.run()

    # websocket.enableTrace(True)
    # ws = websocket.WebSocketApp(
    #     "ws://" + client_ip + ":7071/" + robot_name,
    #     on_open=on_open,
    #     on_message=on_message,
    #     on_error=on_error,
    #     on_close=on_close
    # )

    # ws.run_forever()
    # ws_client = WebSocketClient(robot_name, client_ip)
    # ws_client.start()


    