# SPDX-FileCopyrightText: 2022 Liz Clark for Adafruit Industries
#
# SPDX-License-Identifier: MIT

# Air Quality Sensor

from os import getenv
import supervisor
import microcontroller
import rtc
import time
#import pwmio
import ssl
import board
import gc
#import adafruit_bh1750
import ipaddress
import wifi
import socketpool
import adafruit_requests
import adafruit_connection_manager
import adafruit_minimqtt.adafruit_minimqtt as MQTT
from adafruit_io.adafruit_io import IO_HTTP
from adafruit_io.adafruit_io import IO_MQTT
import neopixel
from nvm_helper import nvm_save_data,nvm_read_data
import busio
from adafruit_pm25.i2c import PM25_I2C
from adafruit_simplemath import map_range
import adafruit_bmp280

# Official AQI colors
GREEN = (0, 255, 0)
YELLOW = (255, 255, 0)
ORANGE = (255, 126, 0)
RED = (255, 0, 0)
PURPLE = (143,63,151)
MAROON = (126,0,35)
BLACK = (0,0,0)

# Define helpers
r = rtc.RTC()
#i2c = board.STEMMA_I2C()
i2c = busio.I2C(board.SCL1, board.SDA1, frequency=100000)
#BH1750_sensor = adafruit_bh1750.BH1750(i2c)
reset_pin = False
pm25 = PM25_I2C(i2c, reset_pin)

BPM280_sensor = adafruit_bmp280.Adafruit_BMP280_I2C(i2c)
BPM280_sensor.sea_level_pressure = 1013.25 # need a presure at sea level reading, which changes

version = "0.0"
debug = False
neopixel_on = True
function = "None"
BoardName = "Air-Quality"
force_error = False
#force_error = True
#xx
UTC_offset = "Z"
night_light_off = True
sauna_display_on = False
idle_time_off = False
wheel_offset = 0
last_msg_time = 0
last_temp_time = 0
last_temperature = 0
current_color = "#000000"
current_white = "OFF"
current_brightness = 0
last_reported_aqi = 0
aqi_PM25 = -1
aqi_PM25_category = "unknown"
aqi_PM10 = -1
aqi_PM10_category = "unknown"
PM_dict = {}
last_PM_dict = {}

# Get WiFi details, ensure these are setup in settings.toml
ssid = getenv("CIRCUITPY_WIFI_SSID")
password = getenv("CIRCUITPY_WIFI_PASSWORD")
aio_username = getenv("ADAFRUIT_AIO_USERNAME")
aio_key = getenv("ADAFRUIT_AIO_KEY")

pixel = neopixel.NeoPixel(board.NEOPIXEL, 1)
if neopixel_on:
    pixel.brightness = 0.6
else:
    pixel.brightness = 0

if None in [ssid, password, aio_username, aio_key]:
    raise RuntimeError(
        "WiFi settings are kept in settings.toml, "
        "please add them there. The settings file must contain "
        "'CIRCUITPY_WIFI_SSID', 'CIRCUITPY_WIFI_PASSWORD', "
        "'ADAFRUIT_AIO_USERNAME' and 'ADAFRUIT_AIO_KEY' at a minimum."
    )

# Define callback functions which will be called when certain MQTT events happen.
def connected(client):
    # Connected function will be called when the client is connected to Adafruit IO.
    # This is a good place to subscribe to feed changes.  The client parameter
    # passed to this function is the Adafruit IO MQTT client so you can make
    # calls against it easily.
    print("Connected to Adafruit IO!  Listening for DemoFeed changes...")

def subscribe(client, userdata, topic, granted_qos):
    # This method is called when the client subscribes to a new feed.
    print(f"Subscribed to {topic} with QOS level {granted_qos}")

def unsubscribe(client, userdata, topic, pid):
    # This method is called when the client unsubscribes from a feed.
    print(f"Unsubscribed from {topic} with PID {pid}")

def disconnected(client):
    # Disconnected function will be called when the client disconnects.
    print("Disconnected from Adafruit IO!")

def publish(client, userdata, topic, pid):
    # This method is called when the client publishes data to a feed.
    print(f"Published to {topic} with PID {pid}")
    if userdata is not None:
        print("Published User data: ", end="")
        print(userdata)

def on_message(client, feed_id, payload):
    # Message function will be called when a subscribed feed has a new value.
    # The feed_id parameter identifies the feed, and the payload parameter has
    # the new value.
    print(f"Feed {feed_id} received new value: {payload}")

def on_West_Beam_Remote_msg(client, topic, message):
    global remote_dict
    global last_msg_time
    last_msg_time = time.time()

    if "press-count" in remote_dict:
        remote_dict["press-count"] += 1
    else:
        remote_dict.update({"press-count":1})

    #match message:
        #case 0:
    button = int(message)
    if button == 0:
        if "volume" in remote_dict:
            remote_dict["volume"] -= 2
        else:
            remote_dict.update({"volume":-2})
    elif button == 1: # play/pause
        print("processing message 1")
        if "play" in remote_dict:
            if remote_dict["play"]:
                remote_dict["play"] = False
            else:
                remote_dict["play"] = True
        else:
            remote_dict.update({"play":True})
    elif button == 2:
        if "volume" in remote_dict:
            remote_dict["volume"] += 2
        else:
            remote_dict.update({"volume":2})
    elif button == 4:
        if "setup" in remote_dict:
            remote_dict["setup"] = True
        else:
            remote_dict.update({"setup":True})

    print("West-Beam-Remote: ", remote_dict,"Button",button)

# Defines for AQI calculations
def calculate_PM25_aqi(pm_sensor_reading):
    """Returns a calculated air quality index (AQI)
    and category as a tuple.
    NOTE: The AQI returned by this function should ideally be measured
    using the 24-hour concentration average. Calculating a AQI without
    averaging will result in higher AQI values than expected.
    :param float pm_sensor_reading: Particulate matter sensor value.

    """
    # Check sensor reading using EPA breakpoint (Clow-Chigh)
    if 0.0 <= pm_sensor_reading <= 9.0:
        # AQI calculation using EPA breakpoints (Ilow-IHigh)
        aqi_val = map_range(int(pm_sensor_reading), 0, 9, 0, 50)
        aqi_cat = "Good"
    elif 9.1 <= pm_sensor_reading <= 35.4:
        aqi_val = map_range(int(pm_sensor_reading), 9, 35, 51, 100)
        aqi_cat = "Moderate"
    elif 35.5 <= pm_sensor_reading <= 55.4:
        aqi_val = map_range(int(pm_sensor_reading), 36, 55, 101, 150)
        aqi_cat = "Unhealthy for Sensitive Groups"
    elif 55.5 <= pm_sensor_reading <= 125.4:
        aqi_val = map_range(int(pm_sensor_reading), 56, 125, 151, 200)
        aqi_cat = "Unhealthy"
    elif 125.5 <= pm_sensor_reading <= 225.4:
        aqi_val = map_range(int(pm_sensor_reading), 126, 225, 201, 300)
        aqi_cat = "Very Unhealthy"
    elif 225.5 <= pm_sensor_reading <= 325.4: # my data sheet has no high breakpoint
        aqi_val = map_range(int(pm_sensor_reading), 226, 350, 301, 500)
        aqi_cat = "Hazardous"
    elif 325.5 <= pm_sensor_reading:
        aqi_val = 501 #map_range(int(pm_sensor_reading), 351, 500, 401, 500)
        aqi_cat = "Hazardous"
    else:
        print("")
        print("Invalid PM2.5 concentration",pm_sensor_reading)
        aqi_val = -1
        aqi_cat = None
    return round(aqi_val), aqi_cat

def calculate_PM10_aqi(pm_sensor_reading):
    """Returns a calculated air quality index (AQI)
    and category as a tuple.
    NOTE: The AQI returned by this function should ideally be measured
    using the 24-hour concentration average. Calculating a AQI without
    averaging will result in higher AQI values than expected.
    :param float pm_sensor_reading: Particulate matter sensor value.

    """
    pm_sensor_reading = int(pm_sensor_reading)

    # Check sensor reading using EPA breakpoint (Clow-Chigh)
    if 0 <= pm_sensor_reading <= 54:
        # AQI calculation using EPA breakpoints (Ilow-IHigh)
        aqi_val = map_range(int(pm_sensor_reading), 0, 54, 0, 50)
        aqi_cat = "Good"
    elif 55 <= pm_sensor_reading <= 154:
        aqi_val = map_range(int(pm_sensor_reading), 55, 154, 51, 100)
        aqi_cat = "Moderate"
    elif 155 <= pm_sensor_reading <= 254:
        aqi_val = map_range(int(pm_sensor_reading), 155, 254, 101, 150)
        aqi_cat = "Unhealthy for Sensitive Groups"
    elif 255 <= pm_sensor_reading <= 354:
        aqi_val = map_range(int(pm_sensor_reading), 255, 354, 151, 200)
        aqi_cat = "Unhealthy"
    elif 355 <= pm_sensor_reading <= 424:
        aqi_val = map_range(int(pm_sensor_reading), 355, 424, 201, 300)
        aqi_cat = "Very Unhealthy"
    elif 425 <= pm_sensor_reading <= 604: # my data sheet has no high breakpoint
        aqi_val = map_range(int(pm_sensor_reading), 425, 604, 301, 500)
        aqi_cat = "Hazardous"
    elif 605 <= pm_sensor_reading:
        aqi_val = 501 #map_range(int(pm_sensor_reading), 351, 500, 401, 500)
        aqi_cat = "Hazardous"
    else:
        print("")
        print("Invalid PM10 concentration",pm_sensor_reading)
        aqi_val = -1
        aqi_cat = None
    return round(aqi_val), aqi_cat

def sample_aq_sensor(debug):
    global PM_dict
    """Samples PM2.5 sensor
    over a 2.3 second sample rate.

    {'pm10 env': 2, 'pm100 env': 2, 'pm100 standard': 2, 'particles 03um': 519, 'pm25 standard': 2, 'particles 10um': 6,
    'pm10 standard': 2, 'pm25 env': 2,
    'particles 05um': 144, 'particles 25um': 0, 'particles 100um': 0, 'particles 50um': 0}
    """
    # initial timestamp
    time_start = time.monotonic()
    # sample pm2.5 sensor over 2.3 sec sample rate
    while time.monotonic() - time_start <= 2.3: #23.0:
        try:
            aqdata = pm25.read()

            if "pm100 env" not in PM_dict:
                PM_dict = {"pm25 env":0,"pm10 env":0,"pm100 env":0,"PM_sample_count":0}
                PM_dict.update({"particles 03um":0,"particles 10um":0,"particles 05um":0})
                PM_dict.update({"particles 25um":0,"particles 100um":0,"particles 50um":0})

            for i in PM_dict:
                if i == "PM_sample_count":
                    PM_dict[i] += 1
                else:
                    PM_dict[i] += aqdata[i]
        except RuntimeError:
            #print("Unable to read from sensor, retrying...")
            continue
        # pm sensor output rate of 1s
        time.sleep(1)
    if debug:
        print(PM_dict)
        print()
        print("Concentration Units (standard)")
        print("---------------------------------------")
        print(
            "PM 1.0: %d\tPM2.5: %d\tPM10: %d"
            % (aqdata["pm10 standard"], aqdata["pm25 standard"], aqdata["pm100 standard"])
        )
        print("Concentration Units (environmental)")
        print("---------------------------------------")
        print(
            "PM 1.0: %d\tPM2.5: %d\tPM10: %d"
            % (aqdata["pm10 env"], aqdata["pm25 env"], aqdata["pm100 env"])
        )
        print("---------------------------------------")
        print("Particles > 0.3um / 0.1L air:", aqdata["particles 03um"])
        print("Particles > 0.5um / 0.1L air:", aqdata["particles 05um"])
        print("Particles > 1.0um / 0.1L air:", aqdata["particles 10um"])
        print("Particles > 2.5um / 0.1L air:", aqdata["particles 25um"])
        print("Particles > 5.0um / 0.1L air:", aqdata["particles 50um"])
        print("Particles > 10 um / 0.1L air:", aqdata["particles 100um"])
        print("---------------------------------------")

# Defines for the board's functions
def setboardtime():
    # calculate time offset from UTC and set RC to local time
    global function
    global UTC_offset
    function = "receive_time"
    web_time_struct = io.receive_time("UTC")
    web_local_time_struct = io.receive_time()
    utc_time = time.mktime(web_time_struct)
    local_time = time.mktime(web_local_time_struct)
    function = "unknown"
    print(utc_time,local_time,(local_time - utc_time)/3600,round((local_time - utc_time)/3600,1))

    hour_offset = round((local_time - utc_time)/3600,1)
    if hour_offset < 0:
        offset_sign = "-"
        hour_offset *= -1
    else:
        offset_sign = "+"
    UTC_offset = "%s%.2d" %(offset_sign,hour_offset)

    print("hours from UTC: ",UTC_offset)
    r.datetime = web_local_time_struct

def iso_to_unix(iso_time):
    #convert iso format (2025-07-23T18:03:41Z) to a unix time
    split_date = iso_time.split("-") # year [0] month [1]
    split_day = split_date[2].split("T") # day [0]
    split_time = split_day[1].split(":") # hour [0] minute [1]
    split_second = split_time[2][:2] # should select the first 2 characters only

    time_struct = (int(split_date[0]),int(split_date[1]),int(split_day[0]),int(split_time[0]),
        int(split_time[1]),int(split_second),0,-1,-1)

    return time.mktime(time_struct)

def time_to_iso(unix_time):
    time_struct = time.localtime(unix_time)
    time_str = "%.4d-%.2d-%.2dT%.2d:%.2d:%.2d%s" %(int(time_struct.tm_year),int(time_struct.tm_mon),
        int(time_struct.tm_mday),int(time_struct.tm_hour),int(time_struct.tm_min),int(time_struct.tm_sec),UTC_offset)
    return time_str

def handle_prior_error(report_boot: bool = False):
    global function
    global force_error
    nvm_string = nvm_read_data(verbose=False)
    if len(nvm_string):
        #report then clear NVM contents
        function = "sending board-troubles"
        nvm_string.update({"Board Name":BoardName})
        io.send_data("board-troubles", str(nvm_string))
        function = "unknown"
        print("Clearing NVM")
        nvm_save_data("",test_run=False,verbose=False)
        force_error = False
    elif report_boot:
        send_data = ({"date":time_to_iso(time.time()),"Board Name":BoardName,"Exception":"booted"})
        function = "sending board-troubles"
        io.send_data("board-troubles", str(send_data))
        function = "unknown"

print("nvm contains [",nvm_read_data(verbose=False),"]")

if debug:
    sample_aq_sensor(debug)

print("Connecting to WiFi: allocated: ",gc.mem_alloc()," free: ", gc.mem_free())

#  connect to your SSID
try:
    function = "wifi.radio.connect"
    wifi.radio.connect(ssid, password)
    function = "unknown"

    print("Connected to WiFi")
    print()

    pool = socketpool.SocketPool(wifi.radio)
    requests = adafruit_requests.Session(pool, ssl.create_default_context())

    #  prints MAC address to REPL
    #print("My MAC addr:", [hex(i) for i in wifi.radio.mac_address])

    #  prints IP address to REPL
    print(f"My IP address is {wifi.radio.ipv4_address}")

    #  pings Google
    #ipv4 = ipaddress.ip_address("8.8.4.4")
    #print("Ping google.com: %f ms" % (wifi.radio.ping(ipv4)*1000))

    # Initialize an Adafruit IO HTTP API object
    function = "IO_HTTP"
    io = IO_HTTP(aio_username, aio_key, requests)

    handle_prior_error(report_boot=True)

    function = "get_current_usage"
    #usage = io.get_current_usage()
    #print("current io usage ",usage)

    function = "get_user_rate_info"
    #rate_info = io.get_user_rate_info()
    #print(rate_info)

    #the print of user_info hangs Mu
    #function = "get_user_info"
    #user_info = io.get_user_info()
    #print(user_info)
    function = "unknown"

    print()
    print("start time: ",time.time()," timestruct: ",time.localtime())

    setboardtime()

    last_status_time = time.time()

    print("web based time: ",time.time()," timestruct: ",r.datetime," allocated: ",gc.mem_alloc()," free: ", gc.mem_free())

    print(time.time(),time_to_iso(time.time()),iso_to_unix(time_to_iso(time.time())))

    # Create a socket pool and ssl_context
    pool2 = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
    ssl_context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)

    # Initialize a new MQTT Client object
    function = "MQTT.MQTT"
    mqtt_client = MQTT.MQTT(
        broker="io.adafruit.com",
        port=1883, #8883,
        username=aio_username,
        password=aio_key,
        socket_pool=pool2,
        ssl_context=ssl_context,
    )

    # Initialize an Adafruit IO MQTT Client
    function = "IO_MQTT"
    io_MQTT = IO_MQTT(mqtt_client)
    function = "unknown"

    # Connect the callback methods defined above to Adafruit IO
    io_MQTT.on_connect = connected
    io_MQTT.on_disconnect = disconnected
    io_MQTT.on_subscribe = subscribe
    io_MQTT.on_unsubscribe = unsubscribe
    io_MQTT.on_message = on_message
    io_MQTT.on_publish = publish

    # Connect to Adafruit IO
    print()
    print("Connecting to Adafruit IO...")
    io_MQTT.connect()

    # Set up a message handler for the feeds of interest
    #io_MQTT.add_feed_callback("Sauna-Temperature", on_Sauna_Temperature_msg)
    #io_MQTT.add_feed_callback("West-Beam-brightness", on_West_Beam_brightness_msg)
    #io_MQTT.add_feed_callback("West-Beam-Light-Strip", on_West_Beam_Light_Strip_msg)
    #io_MQTT.add_feed_callback("West-Beam-Light-Switch", on_West_Beam_Light_Switch_msg)
    io_MQTT.add_feed_callback("West-Beam-Remote", on_West_Beam_Remote_msg)

    # Initialze the feed dictionaries
    old_time = time.mktime((2020,8,8,21,0,0,0,-1,-1))
    last_msg_time = old_time
    remote_dict = {} # Initialize an empty dictionary

    # Subscribe to all messages on the feeds of interest
    io_MQTT.subscribe("West-Beam-Remote")

    io_MQTT.subscribe_to_errors()
    io_MQTT.subscribe_to_throttling()

    print("MQTT configured: allocated: ",gc.mem_alloc()," free: ", gc.mem_free())
    print()

    # Start a blocking loop to check for new messages
    while True:
        function = "MQTT.loop"
        io_MQTT.loop() # Default timeout is 1 second
        function = "unknown"

        current_time = time.time()

        # check for remote controls
        if "play" in remote_dict:
            play = remote_dict["play"]
        else:
            play = False
        if "setup" in remote_dict:
            report_status = remote_dict["setup"]
            del remote_dict["setup"] #= False
        else:
            report_status = False

        sample_aq_sensor(False)
        if PM_dict["PM_sample_count"] > 200:
            aqi_PM25_reading = PM_dict["pm25 env"]/PM_dict["PM_sample_count"]
            aqi_PM10_reading = PM_dict["pm10 env"]/PM_dict["PM_sample_count"]
            aqi_PM25, aqi_PM25_category = calculate_PM25_aqi(aqi_PM25_reading)
            aqi_PM10, aqi_PM10_category = calculate_PM10_aqi(aqi_PM10_reading)
            if debug:
                #print(time_to_iso(current_time),PM_dict)
                print(time_to_iso(current_time),"PM2.5 average",aqi_PM25_reading,"calculated aqi",aqi_PM25,"category",aqi_PM25_category,
                "; PM10 average",aqi_PM10_reading,"calculated aqi",aqi_PM10,"category",aqi_PM10_category)
            for i in PM_dict:
                if i != "PM_sample_count":
                    last_PM_dict[i] = PM_dict[i] / PM_dict["PM_sample_count"]
            PM_dict = {}

            if aqi_PM25 > aqi_PM10:
                aqi_worse = aqi_PM25
            else:
                aqi_worse = aqi_PM25
            if aqi_worse < 51:
                pixel.fill(GREEN)
            elif aqi_worse < 101:
                pixel.fill(YELLOW)
            elif aqi_worse < 151:
                pixel.fill(ORANGE)
            elif aqi_worse < 201:
                pixel.fill(RED)
            elif aqi_worse < 301:
                pixel.fill(PURPLE)
            else:
                pixel.fill(MAROON)
            pixel.show()

            if last_reported_aqi != aqi_worse:
                last_reported_aqi = aqi_worse
                print(time_to_iso(current_time),"AQI",aqi_worse)
                function = "publish AQI"
                io_MQTT.publish("AQI", aqi_worse)
                function = "unknown"

            print(time_to_iso(current_time),'AQI {}'.format(last_reported_aqi),
                'Temperature: {} degrees F'.format((BPM280_sensor.temperature * 9 / 5) + 32),'Pressure: {}hPa'.format(BPM280_sensor.pressure))

        if report_status or last_status_time + 900 < current_time:
            CPU_temp = (microcontroller.cpu.temperature * 9 / 5) + 32
            if report_status == False:
                last_status_time = current_time
            status_dict = {"PM2.5 AQI":aqi_PM25,"PM2.5 Category":aqi_PM25_category}
            status_dict.update({"PM10 AQI":aqi_PM10,"PM10 Category":aqi_PM10_category})
            status_dict.update({"Temperature":(BPM280_sensor.temperature * 9 / 5) + 32,"Pressure (hPa)":BPM280_sensor.pressure})
            status_dict.update({"Version":version,"CPU Temp":CPU_temp})
            status_dict.update({"Last Message":time_to_iso(last_msg_time)})
            status_dict.update(last_PM_dict)
            status_dict.update(remote_dict)
            dict_to_send = {"date":time_to_iso(current_time),"Board Name":BoardName,"Function":"Status",
                "Exception":str(status_dict)}
            print(time_to_iso(current_time),"Sending Dictionary",dict_to_send)
            function = "publish status dictionary"
            io_MQTT.publish("board-troubles", str(dict_to_send))
            function = "unknown"

        time.sleep(0.5)

except Exception as error:
    try:
        time_str = time_to_iso(time.time())
        dict_to_send = {"date":time_str,"Function":function,"Exception":str(error),"mem alloc":gc.mem_alloc(),"mem free":gc.mem_free()}
        nvm_string = nvm_read_data(verbose=False)
        if debug == False and len(nvm_string) == 0:
            print(time_str,"Saving to NVM: [",dict_to_send,"] debug",debug)
            nvm_save_data(dict_to_send,test_run=debug,verbose=False)
        else:
            print(time_str,"Not saving to NVM: [",dict_to_send,"]")
        time.sleep(60)
        print("reset of some type")
        #raise #This will cause the program to stop running
        supervisor.reload() #This will cause the program to restart, but not to reset the H/W
        #microcontroller.reset() #This will reset the H/W, including the REPL
    except Exception as error:
        microcontroller.reset() #This will reset the H/W, including the REPL
