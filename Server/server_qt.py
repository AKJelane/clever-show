import os
import glob
import math

from PyQt5 import QtWidgets
from PyQt5.QtGui import QStandardItemModel, QStandardItem
from PyQt5.QtCore import Qt, pyqtSlot, pyqtSignal, QObject

from PyQt5.QtWidgets import QFileDialog, QMessageBox

# Importing gui form
from server_gui import Ui_MainWindow

from server import *
from copter_table_models import *
from emergency import *


def confirmation_required(text="Are you sure?", label="Confirm operation?"):
    def inner(f):

        def wrapper(*args, **kwargs):
            reply = QMessageBox.question(
                args[0], label,
                text,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                print("Dialog accepted")
                #print(args)
                return f(args[0])
            else:
                print("Dialog declined")

        return wrapper

    return inner


# noinspection PyArgumentList,PyCallByClass
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.init_ui()

        self.model = CopterDataModel()
        self.proxy_model = CopterProxyModel()
        self.signals = SignalManager()

        self.init_model()
        
        self.show()
        
    def init_model(self):
        self.proxy_model.setDynamicSortFilter(True)
        self.proxy_model.setSourceModel(self.model)

        # Initiate table and table self.model
        self.ui.tableView.setModel(self.proxy_model)
        self.ui.tableView.horizontalHeader().setStretchLastSection(True)
        self.ui.tableView.setSortingEnabled(True)

        # Connect signals to manipulate model from threads
        self.signals.update_data_signal.connect(self.model.update_item)
        self.signals.add_client_signal.connect(self.model.add_client)

        # Connect model signals to UI
        self.model.selected_ready_signal.connect(self.ui.start_button.setEnabled)
        self.model.selected_takeoff_ready_signal.connect(self.ui.takeoff_button.setEnabled)

    def client_connected(self, client: Client):
        self.signals.add_client_signal.emit(CopterData(copter_id=client.copter_id, client=client))

    def init_ui(self):
        # Connecting
        self.ui.check_button.clicked.connect(self.selfcheck_selected)
        self.ui.start_button.clicked.connect(self.send_starttime)
        self.ui.pause_button.clicked.connect(self.pause_resume_selected)
        self.ui.stop_button.clicked.connect(self.stop_all)
        self.ui.emergency_button.clicked.connect(self.emergency)

        self.ui.leds_button.clicked.connect(self.test_leds)
        self.ui.takeoff_button.clicked.connect(self.takeoff_selected)
        self.ui.land_button.clicked.connect(self.land_all)
        self.ui.disarm_button.clicked.connect(self.disarm_all)
        self.ui.flip_button.clicked.connect(self.flip)
        self.ui.action_send_animations.triggered.connect(self.send_animations)
        self.ui.action_send_configurations.triggered.connect(self.send_configurations)
        self.ui.action_send_Aruco_map.triggered.connect(self.send_aruco)

        # Set most safety-important buttons disabled
        self.ui.start_button.setEnabled(False)
        self.ui.takeoff_button.setEnabled(False)

    @pyqtSlot()
    def selfcheck_selected(self):
        for copter in self.model.user_selected():
            client = copter.client

            client.get_response("anim_id", self._set_copter_data, callback_args=(1, copter.copter_id))
            client.get_response("batt_voltage", self._set_copter_data, callback_args=(2, copter.copter_id))
            client.get_response("cell_voltage", self._set_copter_data, callback_args=(3, copter.copter_id))
            client.get_response("selfcheck", self._set_copter_data, callback_args=(4, copter.copter_id))
            client.get_response("time", self._set_copter_data, callback_args=(5, copter.copter_id))

    def _set_copter_data(self, value, col, copter_id):
        row = self.model.data_contents.index(next(
            filter(lambda x: x.copter_id == copter_id, self.model.data_contents)))

        if col == 1:
            data = value
        elif col == 2:
            data = "{}".format(round(float(value), 3))
        elif col == 3:
            batt_percent = ((float(value) - 3.2) / (4.2 - 3.2)) * 100  # TODO config
            data = "{}".format(round(batt_percent, 3))
        elif col == 4:
            data = str(value)
        elif col == 5:
            #data = time.ctime(int(value))
            data = "{}".format(round(float(value) - time.time(), 3))
            if abs(float(data)) > 1:
                Client.get_by_id(copter_id).send_message("repair_chrony")
            #self.signals.update_data_signal.emit(row, col + 1, data2)
        else:
            print("No column matched for response")
            return

        self.signals.update_data_signal.emit(row, col, data)

    @confirmation_required("This operation will takeoff selected copters with delay and start animation. Proceed?")
    @pyqtSlot()
    def send_starttime(self, **kwargs):
        dt = self.ui.start_delay_spin.value()
        for copter in self.model.user_selected():
            if all_checks(copter):
                server.send_starttime(copter.client, dt)

    @confirmation_required("This operation will takeoff copters immediately. Proceed?")
    @pyqtSlot()
    def takeoff_selected(self, **kwargs):
        for copter in self.model.user_selected():
            if takeoff_checks(copter):
                copter.client.send_message("takeoff")

    @confirmation_required("This operation will flip(!!!) copters immediately. Proceed?")
    @pyqtSlot()
    def flip(self, **kwargs):
        for copter in self.model.user_selected():
            if takeoff_checks(copter):
                copter.client.send_message("flip")

    @pyqtSlot()
    def test_leds(self):
        for copter in self.model.user_selected():
            copter.client.send_message("led_test")

    @pyqtSlot()
    def stop_all(self):
        Client.broadcast_message("stop")

    @pyqtSlot()
    def pause_resume_selected(self):
        if self.ui.pause_button.text() == 'Pause':
            for copter in self.model.user_selected():
                copter.client.send_message("pause")
            self.ui.pause_button.setText('Resume')
        else:
            self._resume_selected()

    #@confirmation_required("This operation will resume ALL copter tasks with given delay. Proceed?")
    def _resume_selected(self, **kwargs):
        time_gap = 0.1
        for copter in self.model.user_selected():
            copter.client.send_message('resume', {"time": server.time_now() + time_gap})
        self.ui.pause_button.setText('Pause')

    @pyqtSlot()
    def land_all(self):
        Client.broadcast_message("land")

    @pyqtSlot()
    def disarm_all(self):
        Client.broadcast_message("disarm")

    @pyqtSlot()
    def send_animations(self):
        path = str(QFileDialog.getExistingDirectory(self, "Select Animation Directory"))

        if path:
            print("Selected directory:", path)
            files = [file for file in glob.glob(path + '/*.csv')]
            names = [os.path.basename(file).split(".")[0] for file in files]
            print(files)
            for file, name in zip(files, names):
                for copter in self.model.user_selected():
                    if name == copter.copter_id:
                        copter.client.send_file(file, "animation.csv")  # TODO config
                else:
                    print("Filename has no matches with any drone selected")

    @pyqtSlot()
    def send_configurations(self):
        path = QFileDialog.getOpenFileName(self, "Select configuration file", filter="Configs (*.ini *.txt .cfg)")[0]
        if path:
            print("Selected file:", path)
            sendable_config = configparser.ConfigParser()
            sendable_config.read(path)
            options = []
            for section in sendable_config.sections():
                for option in dict(sendable_config.items(section)):
                    value = sendable_config[section][option]
                    logging.debug("Got item from config:".format(section, option, value))
                    options.append(ConfigOption(section, option, value))

            for copter in self.model.user_selected():
                copter.client.send_config_options(*options)

    @pyqtSlot()
    def send_aruco(self):
        path = QFileDialog.getOpenFileName(self, "Select aruco map configuration file", filter="Aruco map files (*.txt)")[0]
        if path:
            filename = os.path.basename(path)
            print("Selected file:", path, filename)
            for copter in self.model.user_selected():
                copter.client.send_file(path, "/home/pi/catkin_ws/src/clever/aruco_pose/map/animation_map.txt")
                copter.client.send_message("service_restart", {"name": "clever"})

    @pyqtSlot()
    def emergency(self):
        client_row_min = 0
        client_row_max = self.model.rowCount() - 1
        result = -1
        while (result != 0) and (result != 3) and (result != 4):
            # light_green_red(min, max)
            client_row_mid = int(math.ceil((client_row_max+client_row_min) / 2.0))
            print(client_row_min, client_row_mid, client_row_max)
            for row_num in range(client_row_min, client_row_mid):
                self.model.data_contents[row_num].client\
                    .send_message("led_fill", {"green": 255})
            for row_num in range(client_row_mid, client_row_max + 1):
                self.model.data_contents[row_num].client \
                    .send_message("led_fill", {"red": 255})

            Dialog = QtWidgets.QDialog()    
            ui = Ui_Dialog()
            ui.setupUi(Dialog)
            Dialog.show()
            result = Dialog.exec()
            print("Dialog result: {}".format(result))

            if client_row_max != client_row_min:
                if result == 1:
                    for row_num in range(client_row_mid, client_row_max + 1):
                        self.model.data_contents[row_num].client \
                            .send_message("led_fill")
                    client_row_max = client_row_mid - 1
                   
                elif result == 2:
                    for row_num in range(client_row_min, client_row_mid):
                        self.model.data_contents[row_num].client \
                            .send_message("led_fill")
                    client_row_min = client_row_mid

        if result == 0:
            Client.broadcast_message("led_fill")
        elif result == 3:
            for row_num in range(client_row_min, client_row_max + 1):
                self.model.data_contents[row_num].client \
                    .send_message("land")
        elif result == 4:
            for row_num in range(client_row_min, client_row_max + 1):
                self.model.data_contents[row_num].client \
                    .send_message("disarm")


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()

    Client.on_first_connect = window.client_connected

    server = Server(on_stop=app.quit)
    server.start()

    app.exec_()
    server.stop()
    sys.exit()
