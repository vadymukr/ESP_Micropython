
"""
Garden Watering System - v0.3
Date: 20-02:2020

ESP32 ADC
ADC1 - 8 channels, attached to GPIOs - 32-39
Exception GPIOs - 0,2,4,15 - -> used for other purposes

ADC2 - 10 channels, attached to GPIOs - 0,2,4,12-15, 25-27
GPIOs 0,2,15 - might be strapping -> can't be used 

PMW GPIO - 5

Internal hall sensor is using pins GPIOs 36, 39 (channels 0 and 3)
"""


"""
In this example the following pin has been used:
for ADC used GPIOs - 34, 35
for LEDs used GPIO  - 2,16,17,18,19
for controlling water pump used GPIO - 21 (22,23 - can be used)
for rising alarm flag GPIO - 23
for wakeup on external trigeer, pin14 on ground 

"""

from machine import Pin, ADC, deepsleep, I2C
# from time import sleep, time, ticks_ms, ticks_diff, ticks_us, ticks_add
import esp32
# import ssd1306
# import _thread
import dht
import utime

# Defining water sensor reading interval, in seconds
READING_INTERVAL = 2 #  seconds
ALARM = True #uncomment for production
#ALARM = False #Comment fot production
THREAD_RUNNING = False
PWORK  = 30 # seconds
DEBUG = True
# 1 day in ms = 86400000
DEEPSLEEP_TIME = 500 # Time interval, in millisecons, when system will wockup
DEEPSLEEP_TIMEOUT = 100 # Time Interveal,in seconds, when system will go to deep sleep mode


# Initialisation of ADC
ADC.width(ADC.WIDTH_12BIT)
#Humidity sensor (hs) - object for humidity sensor connected to ADC
hs = ADC(Pin(34)) #for the board
# hs = ADC(Pin(32)) #for the debug board
hs.atten(ADC.ATTN_11DB)

#Tank water level (twl) - object for reading water level from the water tank
twl = ADC(Pin(35))
twl.atten(ADC.ATTN_11DB)

#Pin used for controlling war Pump (wp)
# wp_led = Pin(16,Pin.OUT) # no control. used directly from motor
wp_ctrl = Pin(18,Pin.OUT)

#Pin used for flashing alarm
alarm_led = Pin(23,Pin.OUT)

#wakeup= Pin(14, mode = Pin.IN)

#Humidity and temprature sensor DHT11 initialisation
dht11 = dht.DHT11(Pin(14))

#Amount of water in the soil. The bigger value - > less water in the soil.
L1 = range(2500, 4100)
L2 = range(1780, 2500)
L3 = range(1400, 1780)
L4 = range(0,1399)

#Initialisation of pins used to display level of water in the soil
led0 = Pin(21,Pin.OUT) # for L1
led1 = Pin(22,Pin.OUT) # for L2
led2 = Pin(19,Pin.OUT) # for L3

RedColor = '\033[91m'
ColorOff = '\033[0m'

LastDHT11Read = utime.time() 
dht_period = 2 #seconds

def readDHT11():
    try:
        global LastDHT11Read,h, t
        d_dht = utime.time()- LastDHT11Read
        if d_dht >=dht_period  :
            LastDHT11Read  = utime.time()
            dht11.measure()
            t= dht11.temperature()
            h=dht11.humidity()
            temp= t*(9/5) +32.0
            print("Temprature,Humidity", t,h)
            print("Temprature: %3.1f C" %t)
#             print("Temprature: %3.1f F" %temp)
            print("humidity: %3.1f %%" %h)
#         else:
#             print('LastDHT11ReadInterval = ' ,utime.time()- LastDHT11Read )
#             return [0,0]
    except OSError as e:
        print("Failed to read sensor")
        
#checking the level of water in the water tank
def tankWaterLevel():
    twl_value = twl.read()
#     print("Tank water Level = ", twl_value)
    return twl_value

#Checking the soil humidity
def plantWaterLevel():
    hs_value = hs.read()
#     print("Soil water Level = ", hs_value)
    return hs_value
  
leds = [0,0,0]

def displayLED(disp):
    led0.value(disp[0]) #level 0
    led1.value(disp[1]) #level 1
    led2.value(disp[2]) #level 2  

def riseAlarm():
    global  _plantWaterLevel, _tankWaterLevel
#     print(RedColor+'riseAlarm(): Checking if water tank empty or water tank sensor is connected _tankWaterLevel  = ', _tankWaterLevel, ColorOff)
    global ALARM
    if _tankWaterLevel in L1:
        ALARM= False
#         print('riseAlarm()_tankWaterLevel in L1 : Water tank level is low {}, it in the range L1 {}. Alarm  = {}.'.format(L1, _tankWaterLevel,ALARM))
        ledFlashAlarm()            
    else:
#         print('riseAlarm()_else : So far so good, sensor value = ', _tankWaterLevel,ALARM)
        ALARM = True
        alarm_led.value(0)

next_alarm_time_ms = utime.ticks_ms()
alarm_period = 1000
def ledFlashAlarm():
    global alarm_period, next_alarm_time_ms
#     print('Alarm checks',utime.ticks_ms()- next_alarm_time_ms,utime.ticks_ms(),next_alarm_time_ms)
    t_differ = utime.ticks_diff(utime.ticks_ms(),next_alarm_time_ms)
    if t_differ>=0:
#         print('Alarm True, Delta time is 1000 ms',utime.ticks_ms()-next_alarm_time_ms)
        next_alarm_time_ms = utime.ticks_add(utime.ticks_ms(),alarm_period)
        alarm_led.value(not alarm_led.value())


next_hm_time_ms = utime.ticks_ms()
hm_period = 500
def ledFlashLowHumid():
    global hm_period, next_hm_time_ms
#     print('Hunid TEst checks',utime.ticks_ms()- next_hm_time_ms,utime.ticks_ms(),next_hm_time_ms, utime.time())
#     t_differ = utime.ticks_diff(utime.ticks_ms(),next_alarm_time_ms)
    t_differ = utime.ticks_diff(utime.ticks_ms(), next_hm_time_ms)
    if t_differ>=0:
#         print('Alarm True, Delta time is',utime.ticks_ms()-next_hm_time_ms)
        next_hm_time_ms = utime.ticks_add(utime.ticks_ms(),hm_period)
        led0.value(not led0.value())

#turnin on the pump and rising the LED flag in the list when pump is working
def pumpON():
#     print('pumpON(): Switching Pump ON')
#     wp_led.value(1)
    wp_ctrl.value(1)
   # leds[3]=1

#turning off the pump and dimming the LED flag in the list when pump is off
def pumpOFF():
#     print('pumpOFF(): Switching Pump OFF')
#     wp_led.value(0)
    wp_ctrl.value(0)
   # leds[3]=0
 
def pumpControl(pump):
    #if pump False  - switching on the pump :
    if pump == False and ALARM == True :
#         print('pumpControl(pump)_pump: Switching ON. Pump control state = ' , ALARM)
        pumpON()
#     print('PumpControl if check comleted', pump, ALARM)    
    #if pump True  - switching off the pump :          
    if pump == True and ALARM == False:
#         print('pumpControl(pump)_pump:: Pump is going to be OFF, control state = ' , ALARM)
        pumpOFF()
#     print('PumpControl else if2 check comleted',pump, ALARM)
    if pump == False and ALARM == False:
#         print('pumpControl(pump)_pump:: Pump is going to be OFF, control state = ' , ALARM)
        pumpOFF()
#     print('PumpControl else if3 check comleted',pump, ALARM)
    if pump == True and ALARM == True:
#         print('pumpControl(pump)_pump:: Pump is going to be OFF, control state = ' , ALARM)
        pumpOFF()
#     print('PumpControl else if4 check comleted',pump, ALARM) 

"""System will go in to sleep mode after defined inteval "DEEPSLEEP_TIMEOUT",
for a time "DEEPSLEEP_TIME" without current system state."""

def deepSleep(start_time):
    if (time()- start_time) > DEEPSLEEP_TIMEOUT:
        deepsleep(DEEPSLEEP_TIME)
        """print('DeepSleep Timeout = ', DEEPSLEEP_TIMEOUT) 
        print('########################################\n')
        print("Debug.Time, Goint to sleeep",(time()-start_time ))
        print('########################################\n')
        print('goint to sleep for = ', DEEPSLEEP_TIME)"""

def display():
    global _plantWaterLevel, _tankWaterLevel, h, t
    oled.fill(0)
    #first parameter in Oled: "string to display", second: (X axis) Distance from left to right in pixels: third: (Y axis) from top to bottom
    # Max number of symbols: Xmax=16 Ymax=50
    oled.text('ALARM - '+ str(ALARM),0, 0)
    oled.text('Plant wl '+str((1-_plantWaterLevel/4000)*100)+'%', 1, 10)
    oled.text('Tank wl ' +str((1-_tankWaterLevel/4000)*100), 1, 20)
    #Reading sensor velues to the list
#     humitity_temprature=readDHT11()
    oled.text('Temperature '+str(t)+'C', 0, 30)
    oled.text('Humidity '+str(h)+'%', 0, 40)
    #oled.text('Hours '+str(time()/3600), 0, 30)
    #oled.text('minutes'+str(time()/60), 0, 40)
    #oled.text('Systime'+str(time()), 0, 50)
    oled.show()
    
def systemWakeUP():
    #esp32.wake_on_ext0(pin = wakeup, level = esp32.WAKEUP_ANY_HIGH)
    esp32.wake_on_ext0(pin = wakeup, level = esp32.WAKEUP_ALL_LOW)
    print('systemWakeUP(): I woke up')

def selfCheck():
    print('selfCheck(): Performing Self Check')
    alarm_led.value(1)
    #wp_led.value(1)
    wp_ctrl.value(1) # to be deleted when real pump will be connected
    led1.value(1)
    led2.value(1)
    led3.value(1)
    sleep(5)
    #wp_led.value(0)
    wp_ctrl.value(0) # to be deleted when real pump will be connected
    led1.value(0)
    led2.value(0)
    led3.value(0)
    alarm_led.value(0)

next_adc_ms = utime.ticks_ms()
adc_read_period = 200

def readADC():
    global adc_read_period, next_adc_ms,_plantWaterLevel,_tankWaterLevel
#     print('Alarm checks',utime.ticks_ms()- next_adc_ms,utime.ticks_ms(),next_adc_ms)
    t_differ = utime.ticks_diff(utime.ticks_ms(),next_adc_ms)
    if t_differ>=0:
#         print('____________________Reading ADC, Delta time is 200 ms________________',utime.ticks_ms()-next_adc_ms)
        next_adc_ms = utime.ticks_add(utime.ticks_ms(),adc_read_period )
        _plantWaterLevel= plantWaterLevel()
        _tankWaterLevel = tankWaterLevel()

i2 = I2C(-1, scl=Pin(15), sda=Pin(4))
oled_width = 128
oled_height = 64
# oled = ssd1306.SSD1306_I2C(oled_width, oled_height, i2)

#systemWakeUP()
#selfCheck()
i = 0
_plantWaterLevel=0
_tankWaterLevel = 0
h = 0 #humitity
t = 0 #temprature

d_next = utime.ticks_ms()
d_period = 500
while True:
#     print('########################################\n')
#     print('Run Cycle ', i)
    readADC()
    i+=1
    riseAlarm()
#     readDHT11()
#checking plant water level if in L1 - very dry
#     print('chacking if1 starting pump ', ALARM,_plantWaterLevel) 
    if _plantWaterLevel in L1:
        pump = False
#         print('if1 starting pump ', pump,_plantWaterLevel) 
        pumpControl(pump)
        ledFlashLowHumid()
#         leds[0] = 0
        leds[1] = 0
        leds[2] = 0
#     print('chacking if2 starting pump ', ALARM,_plantWaterLevel)
    if _plantWaterLevel in L2:
        pump = True
#         print('if2 starting pump ', pump, _plantWaterLevel) 
        pumpControl(pump)
        leds[0] = 1
        leds[1] = 1
        leds[2] = 0
#     print('chacking if3 starting pump ', ALARM, _plantWaterLevel)
    if _plantWaterLevel in L3:
        pump = True
#         print('if3 starting pump ', pump, _plantWaterLevel) 
        pumpControl(pump)
        leds[0] = 1
        leds[1] = 1
        leds[2] = 1           
    
    display_d = utime.ticks_ms()-d_next
    if display_d>=d_period:
#         display()
        displayLED(leds)
        d_next=utime.ticks_ms()
                
#     print('going to sleep for {} interval'.format(READING_INTERVAL))
    #deepSleep(start_time)
print('*************************Exiting Programm, Please fix the issue and restart program**********************')

