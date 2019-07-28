import os
import time
import rospy
import logging

from FlightLib import FlightLib
from FlightLib import LedLib

import client

import messaging_lib as messaging
import tasking_lib as tasking
import animation_lib as animation

# logging.basicConfig(  # TODO all prints as logs
#    level=logging.DEBUG, # INFO
#    format="%(asctime)s [%(name)-7.7s] [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s",
#    handlers=[
#        logging.StreamHandler(),
#    ])

logger = logging.getLogger(__name__)


# import ros_logging

class CopterClient(client.Client):
    def load_config(self):
        super(CopterClient, self).load_config()
        self.FRAME_ID = self.config.get('COPTERS', 'frame_id')
        self.TAKEOFF_HEIGHT = self.config.getfloat('COPTERS', 'takeoff_height')
        self.TAKEOFF_TIME = self.config.getfloat('COPTERS', 'takeoff_time')
        self.SAFE_TAKEOFF = self.config.getboolean('COPTERS', 'safe_takeoff')
        self.RFP_TIME = self.config.getfloat('COPTERS', 'reach_first_point_time')
        self.LAND_TIME = self.config.getfloat('COPTERS', 'land_time')

        self.X0_COMMON = self.config.getfloat('COPTERS', 'x0_common')
        self.Y0_COMMON = self.config.getfloat('COPTERS', 'y0_common')
        self.X0 = self.config.getfloat('PRIVATE', 'x0')
        self.Y0 = self.config.getfloat('PRIVATE', 'y0')
        self.USE_LEDS = self.config.getboolean('PRIVATE', 'use_leds')
        self.LED_PIN = self.config.getint('PRIVATE', 'led_pin')

    def on_broadcast_bind(self):
        configure_chrony_ip(self.server_host)
        restart_service("chrony")

    def start(self, task_manager_instance):
        client.logger.info("Init ROS node")
        rospy.init_node('Swarm_client', anonymous=True)
        if self.USE_LEDS:
            LedLib.init_led(self.LED_PIN)

        task_manager_instance.start()

        super(CopterClient, self).start()


def restart_service(name):
    os.system("systemctl restart {}".format(name))


def configure_chrony_ip(ip, path="/etc/chrony/chrony.conf", ip_index=1):
    try:
        with open(path, 'r') as f:
            raw_content = f.read()
    except IOError as e:
        print("Reading error {}".format(e))
        return False

    content = raw_content.split(" ")

    try:
        current_ip = content[ip_index]
    except IndexError:
        print("Something wrong with config")
        return False

    if "." not in current_ip:
        print("That's not ip!")
        return False

    if current_ip != ip:
        content[ip_index] = ip

        try:
            with open(path, 'w') as f:
                f.write(" ".join(content))
        except IOError:
            print("Error writing")
            return False

    return True


@messaging.request_callback("selfcheck")
def _response_selfcheck():
    check = FlightLib.selfcheck()
    return check if check else "OK"


@messaging.request_callback("anim_id")
def _response_animation_id():
    return animation.get_id()


@messaging.request_callback("batt_voltage")
def _response_batt():
    return FlightLib.get_telemetry('body').voltage


@messaging.request_callback("cell_voltage")
def _response_cell():
    return FlightLib.get_telemetry('body').cell_voltage


@messaging.message_callback("test")
def _command_test(**kwargs):
    logger.info("logging info test")
    print("stdout test")


@messaging.message_callback("service_restart")
def _command_service_restart(**kwargs):
    restart_service(kwargs["name"])

@messaging.message_callback("repair_chrony")
def _command_chrony_repair():
    configure_chrony_ip(client.active_client.server_host)
    restart_service("chrony")


@messaging.message_callback("led_test")
def _command_led_test(**kwargs):
    LedLib.chase(255, 255, 255)
    time.sleep(2)
    LedLib.off()


@messaging.message_callback("led_fill")
def _command_led_fill(**kwargs):
    r = kwargs.get("red", 0)
    g = kwargs.get("green", 0)
    b = kwargs.get("blue", 0)

    LedLib.fill(r, g, b)


@messaging.message_callback("flip")
def _copter_flip():
    FlightLib.flip(frame_id=client.active_client.FRAME_ID)

@messaging.message_callback("takeoff")
def _command_takeoff(**kwargs):
    task_manager.add_task(time.time(), 0, animation.takeoff,
                          task_kwargs={
                              "z": client.active_client.TAKEOFF_HEIGHT,
                              "timeout": client.active_client.TAKEOFF_TIME,
                              "safe_takeoff": client.active_client.SAFE_TAKEOFF,
                              "use_leds": client.active_client.USE_LEDS,
                          }
                          )


@messaging.message_callback("land")
def _command_land(**kwargs):
    task_manager.reset()
    task_manager.add_task(0, 0, animation.land,
                          task_kwargs={
                              "z": client.active_client.TAKEOFF_HEIGHT,
                              "timeout": client.active_client.TAKEOFF_TIME,
                              "frame_id": client.active_client.FRAME_ID,
                              "use_leds": client.active_client.USE_LEDS,
                          }
                          )


@messaging.message_callback("disarm")
def _command_disarm(**kwargs):
    task_manager.reset()
    task_manager.add_task(-5, 0, FlightLib.arming_wrapper,
                          task_kwargs={
                              "state": False
                          }
                          )


@messaging.message_callback("stop")
def _command_stop(**kwargs):
    task_manager.stop()


@messaging.message_callback("pause")
def _command_pause(**kwargs):
    task_manager.pause()


@messaging.message_callback("resume")
def _command_resume(**kwargs):
    task_manager.resume(time_to_start_next_task=kwargs.get("time", 0))


@messaging.message_callback("start")
def _play_animation(**kwargs):
    start_time = float(kwargs["time"])

    anim = animation.AnimationLoader()
    loaded = anim.load_csv(os.path.abspath("animation.csv"))  # TODO config
    if not loaded:
        print("Can't start animation without animation file!")
        return

    print("Start time = {}, wait for {} seconds".format(start_time, time.time() - start_time))

    # todo rotate z and gps transformation

    x0 = client.active_client.X0 + client.active_client.X0_COMMON
    y0 = client.active_client.Y0 + client.active_client.Y0_COMMON
    z0 = 0  # TODO
    anim.offset([x0, y0, z0])

    fps = anim.fps if anim.fps is not None else 8
    frame_delay = 1/fps
    task_kwargs = {
        "frame_id": client.active_client.FRAME_ID,
        "use_leds": client.active_client.USE_LEDS,
        "flight_func": FlightLib.navto,
    }

    player = animation.TaskingAnimationPlayer(anim, frame_delay, task_manager, task_kwargs)

    task_manager.add_task(start_time, 0, animation.takeoff,
                          task_kwargs={
                              "z": client.active_client.TAKEOFF_HEIGHT,
                              "timeout": client.active_client.TAKEOFF_TIME,
                              "safe_takeoff": client.active_client.SAFE_TAKEOFF,
                              # "frame_id": client.active_client.FRAME_ID,
                              "use_leds": client.active_client.USE_LEDS,
                          }
                          )

    rfp_time = start_time + client.active_client.TAKEOFF_TIME
    task_manager.add_task(rfp_time, 0, animation.point_flight,
                          task_kwargs={
                              "frame": anim[0],
                              "frame_id": client.active_client.FRAME_ID,
                              "use_leds": client.active_client.USE_LEDS,
                              # todo use yaw
                              "flight_func": FlightLib.reach_point,
                          }
                          )

    frame_time = rfp_time + client.active_client.RFP_TIME

    player.execute_animation(frame_time)  # animation executor (adding frame tasks)

    land_time = player.frame_time + client.active_client.LAND_TIME
    task_manager.add_task(land_time, 0, animation.land,
                          task_kwargs={
                              "timeout": client.active_client.TAKEOFF_TIME,
                              "frame_id": client.active_client.FRAME_ID,
                              "use_leds": client.active_client.USE_LEDS,
                          },
                          )


if __name__ == "__main__":
    copter_client = CopterClient()
    task_manager = tasking.TaskManager()

    copter_client.start(task_manager)

    # ros_logging.route_logger_to_ros()
    # ros_logging.route_logger_to_ros("__main__")
    # ros_logging.route_logger_to_ros("client")
    # ros_logging.route_logger_to_ros("messaging")
