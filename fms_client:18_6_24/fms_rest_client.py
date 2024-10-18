#!/usr/bin/python

from autobahn.twisted.websocket import WebSocketClientProtocol
from twisted.internet.protocol import ReconnectingClientFactory
from autobahn.twisted.websocket import WebSocketClientFactory
from autobahn.twisted.websocket import WebSocketClientFactory, WebSocketClientProtocol, connectWS
from twisted.python import log
from twisted.internet import reactor, ssl

import rospy
from std_msgs.msg import String
from rospy_message_converter import json_message_converter, message_converter
# import message_filters

from rostopic import get_topic_type
from rosservice import get_service_type

from autobahn.websocket.compress import PerMessageDeflateOffer, \
    PerMessageDeflateResponse, \
    PerMessageDeflateResponseAccept

import json
import sys
import time

from threading import Lock, Thread
# import websocket, rel
# import websocket
try:
    import thread
except ImportError:
    import _thread as thread

import datetime
import roslib
# import roslibpy
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

    def __init__(self, name):
        self.task_manager_topics = [
        ]
        self.subscriber_list = {}
        self.subscriber_rate = {}
        self.robot_name = name
        self.callback_last_sent = {}
        # self.roslib = roslibpy.Ros(host='localhost', port=9090)
        # thr = Thread(target=self.roslib.run)
        # thr.start()
        # ws_client = protocol

    def update_client(self, protocol):
        self.ws_client = protocol
    
    # def __str__(self):
    #     return self.robot_name

    
    def send_message(self, *args):
        try:
            # print(args)
            
            time = datetime.datetime.utcnow()
            msg = {
                "op": "SUB_MSGS",
                "topic": args[1],
                "robot_name": self.robot_name,
                "datetime": str(time),
                "msg": json_message_converter.convert_ros_message_to_json(args[0])
            }
            # print(msg)
            #print("Callback called")
            #self.ws_client.sendMessage(json.dumps(msg, ensure_ascii=False).encode('utf8'), isBinary=False)
            self.ws_client.callback(json.dumps(msg, ensure_ascii=False).encode('utf8'))
            # self.ws_client.callback(json.dumps(msg, ensure_ascii=False).encode('utf8'), time=time)
            # self.ws_client.callback(json.dumps(msg))
            # time_2 = datetime.datetime.now()
            # print((time_2 - time).total_seconds())
            # time.sleep(0.1)
            # send to nodejs serve
            # print(msg)
        except Exception as e:
            logger_info_configuration("EXCEPTION IN CALLBACK: ",str(e))

    def subscribe_callback(self, msg, *args):
        # thread = Thread(target=self.send_message, args=(msg, args[0],))
        # thread.start()
        # rate = self.subscriber_rate[args[0]]
        last_sent = self.callback_last_sent.get(args[0], None)        
        if last_sent and ((time.time() - last_sent) < 0.25):
                        return
        self.callback_last_sent[args[0]] = time.time()
        self.send_message(msg, args[0])
        # if fms_filtered:
        #         self.robot_pos_prev = time.time()

    def init_subscription(self):
        logger_info_configuration(self)
        list_sub = self.subscriber_list.keys()
        logger_info_configuration(list_sub)
        for j in list_sub:
            # function_this = lambda msg: self.subscribe_callback(msg, "/" + self.robot_name + "/" + j)
            self.subscribe_topics(j)
            # self.subscribe_topics("/" + self.robot_name + "/" + "robot_pose", lambda msg: self.subscribe_callback(msg, "/" + self.robot_name + "/" + "robot_pose"))

    # def unsubscribe_all(self):
    #     for k in self.subscriber_list.keys():
    #         self.subscriber_list[k].unregister()
    #         del self.subscriber_list[k]

    def unsubscribe_topics(self, topic):
        try:
            if self.subscriber_list.get(topic):
                logger_info_configuration("unsubscribing")
                self.subscriber_list[topic].unregister()
                del self.subscriber_list[topic]
        except Exception as e:
            logger_info_configuration(e)
            raise Exception("Unregister failed for %s" % topic)

    # def subscribe(self, topic, callback):
    #     topic_type = get_topic_type(topic)[0]
    
    
    def publish(self, topic, message):
        topic_type = get_topic_type(topic)[0]
        msg_class_ = _load_class(topic_type.partition("/")[0], "msg", topic_type.partition("/")[2])
        logger_info_configuration(topic_type)
        logger_info_configuration(msg_class_)
        logger_info_configuration(message)
        message = convert(message)
        logger_info_configuration(message)
        if (msg_class_._type == topic_type):
            pub = rospy.Publisher(topic, msg_class_, queue_size=10)
            msg = message_converter.convert_dictionary_to_ros_message(topic_type, message)
            msg = convert(msg)
            logger_info_configuration(msg)
            pub.publish(msg)
            # time.sleep(1)
            pub.unregister()
            logger_info_configuration("Published")

    def service_call(self, service, args):
        rospy.wait_for_service(service)
        try:
            logger_info_configuration(service)
            service_type = get_service_type(service)
            logger_info_configuration(service_type)
            msg_class_ = _load_class(service_type.partition("/")[0], "srv", service_type.partition("/")[2])
            # if (msg_class_._type == topic_type) :
            srv = rospy.ServiceProxy(service, msg_class_)
            logger_info_configuration(srv)
            logger_info_configuration(args)
            resp = {}
            if not args:
                resp = srv()
            else:
                resp = srv(json_message_converter.convert_json_to_ros_message(service_type, args))
            logger_info_configuration(resp)            
            msg = {
                    "op": "SERVICE_MSG",
                    "service": service,
                    "result": json_message_converter.convert_ros_message_to_json(resp)
                   }
            logger_info_configuration("sending to root")
            # send to nodejs server
            self.ws_client.sendMessage(json.dumps(msg, ensure_ascii = False).encode('utf8'), isBinary=False)   
        except Exception as e:
            logger_info_configuration(e)
    
    # def subscribe_topic_ros(self, topic):
    #     topic_type = self.roslib.get_topic_type(topic)
    #     if topic_type:
    #         print(topic_type)
    #         sub = roslibpy.Topic(self.roslib, topic, topic_type, reconnect_on_close=True)
    #         sub.subscribe(callback=lambda msg: self.send_message(msg, args=[topic]))
    #         self.subscriber_list[topic] = sub

    def subscribe_topics(self, topic):
        
        try :
            # if not self.subscriber_list :
            #     with _subscriber_list_lock:
                    # for key in self.topics:
                    # topic = "/" + str(self.robot_name) + "/" + str(topic)
                    # self.subscriber_rate[topic] = rate
                    logger_info_configuration(topic)
                    logger_info_configuration(self.subscriber_list.get(topic))
                    if self.subscriber_list.get(topic):
                        unsubscribe = Thread(
                            target=self.unsubscribe_topics,
                            args=(topic,))
                        unsubscribe.start()
                    topic_type = get_topic_type(topic)[0]
                    logger_info_configuration(get_topic_type(topic))
                    if (topic_type is None) :
                        raise Exception("Topic type unavaialble")
                    else:
                        msg_class_ = _load_class(topic_type.partition("/")[0], "msg", topic_type.partition("/")[2])
                        logger_info_configuration(msg_class_)
                        if (msg_class_._type == topic_type) :
                            self.subscriber_list[topic] = rospy.Subscriber(
                                topic, 
                                msg_class_, 
                                self.subscribe_callback, 
                                callback_args=topic,
                                queue_size=1,
                                buff_size=1*1024*10
                                )
                            logger_info_configuration(self.subscriber_list.keys())
                        else:
                            raise Exception("Topic type mismatch")
        except Exception as e:
            logger_info_configuration(e)

    def run_command(self, message):
        try:
            m = json.loads(message)
            op = m.get("OP")

            # if op == "PUBLISH":
            #     topic = m.get("topic")
            #     message = m.get("data")
            #     print(message)
            #     self.publish(topic, message)

            # if op == "SERVICE":
            #     service = m.get("service")
            #     args = m.get("data")
            #     self.service_call(service, args)

            if op == "SUBSCRIBE":
                topic = m.get("topic")
                # rate = m.get("rate", 5)
                logger_info_configuration(topic)
                self.subscribe_topics(topic)

            if op == "UNSUBSCRIBE":
                topic = m.get("topic")
                self.unsubscribe_topics(topic)

        except Exception as e:
            logger_info_configuration(e)

    def task_state_callback(self, robot_state, bms, robot_pose, task_status, error_list, load_status):
        # task_state_callback
        logger_info_configuration("running task state callback")
        data = {}
        data.update(message_converter.convert_ros_message_to_dictionary(robot_state))
        data.update(message_converter.convert_ros_message_to_dictionary(bms))
        data.update(message_converter.convert_ros_message_to_dictionary(robot_pose))
        data.update(message_converter.convert_ros_message_to_dictionary(task_status))
        data.update(message_converter.convert_ros_message_to_dictionary(error_list))
        data.update(message_converter.convert_ros_message_to_dictionary(load_status))

        logger_info_configuration(data)

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
                    logger_info_configuration(self.task_subscriber_list)
                    # ts = message_filters.ApproximateTimeSynchronizer(self.task_subscriber_list, 1, 1, allow_headerless=True)
                    # ts.registerCallback(self.task_state_callback)
        except Exception as e:
            logger_info_configuration(e)


class MyClientProtocol(WebSocketClientProtocol):

    
    def __init__(self):
        
        super(MyClientProtocol, self).__init__()
        ros_client.update_client(self)
        # print(self.ros_client)
    
    
    def onOpen(self):        
        logger_info_configuration("OnOpen!")
        # Subscribes to initial topics on open connection
        # ws_client = self
        if ros_client:
            ros_client.init_subscription()
        thread = {}

    def callback(self, data, time=None):
        #logger_info_configuration("### CALLBACK TRIGGERED!")
        # print(msg)
        #print(type(msg))
        # thread = Thread(target=self.sendMessage, args=(json.dumps(msg),))
        # thread.start()
        if time:
            datetime_now = datetime.datetime.utcnow()
            if (time - datetime_now).total_seconds() > 1:
                logger_info_configuration("SUBSCRIBE MESSAGE CALLBACK ERROR: Time difference is greater than 1 second")
                return None
        self.sendMessage(data)
      
    def onConnect(self, request):
        #return None
        logger_info_configuration("Connected!")
        
        self.factory.resetDelay()

    def onMessage(self, payload, isBinary):
        
        logger_info_configuration("#####  onMessage #######")
        if isBinary:
            logger_info_configuration("Binary message received: {0} bytes".format(len(payload)))
        else:
            logger_info_configuration("Text message received: {0}".format(payload.decode('utf8')))
            if ros_client:
                ros_client.run_command(payload.decode('utf8'))
	        

    def onClose(self, wasClean, code, reason):

        logger_info_configuration("########## onClose ###############")
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
    #     print("thread terminating...")
    # thread.start_new_thread(run, ())
    logger_info_configuration("connection is open")


# class WebSocketClient():

#     def __init__(self, robot_name, client_ip):
        
#         # super(MyClientProtocol, self).__init__()
#         self.robot_name = robot_name
#         self.client_ip = client_ip
#         self.ros_client = RosClient(self.robot_name, self)
#         print(self.ros_client)
#         self.ws = None
#         # self.start(robot_name, client_ip)
#         # self.run_forever()
    
#     def start(self):
#         # websocket.enableTrace(True)
#         self.ws = websocket.WebSocketApp(
#             "ws://" + self.client_ip + ":7072/" + self.robot_name,
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
#         print("Connect is open")
#         self.ros_client.init_subscription()
    
#     def callback(self, data):
#         # print("### CALLBACK TRIGGERED!")
#         # print(data)
#         # msg = {
#         #     "op": "publish",
#         #     "topic": topic,
#         #     "msg": json_message_converter.convert_ros_message_to_json(data)
#         # }
#         # print(msg)
#         # #print(type(msg))
#         if self.ws:
#             self.ws.send(data)
#             pass

#     def on_message(self, ws, message):
#         print(message)
#         # if isBinary:
#         #     print("Binary message received: {0} bytes".format(len(message)))
#         # else:
#         #     print("Text message received: {0}".format(message.decode('utf8')))
#         self.ros_client.run_command(message)
    
#     def on_error(self, ws, error):
#         print("error in connection")
#         print(error)
    
#     def on_close(self, ws):
#         print("Closed connection")




if __name__ == '__main__':

    import sys
    log.startLogging(sys.stdout)

    rospy.init_node('listener', anonymous=True)
    robot_name = sys.argv[1]
    client_ip = sys.argv[2]
    ros_client = RosClient(robot_name)

    # #ws::localhost
    # print(client_ip)
   
    factory = MyClientFactory("wss://" + client_ip +":8001/ws/sub_update/"+ robot_name+ "/")
    # factory.protocol = MyClientProtocol(robot_name, robot_ip)
    factory.protocol = MyClientProtocol
    offers = [PerMessageDeflateOffer()]
    
    factory.setProtocolOptions(
        autoPingInterval=10,
        autoPingTimeout=5,
        perMessageCompressionOffers=offers
    )

    def accept(response):
        if isinstance(response, PerMessageDeflateResponse):
            return PerMessageDeflateResponseAccept(response)
    
    factory.setProtocolOptions(perMessageCompressionAccept=accept)

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


    