import glob, os, shutil, time
from ophyd import Component as Cpt, EpicsSignal, Device
from bluesky.plan_stubs import null, sleep, mv, mvr

from bmm_tools.tools.animated_prompt import PROMPTNC
from BMM.logging import report

from BMM import user_ns as user_ns_module
user_ns = vars(user_ns_module)


class USBVideo(Device):
    '''Simple class for recording videos via the USB cameras in the
    hutch.

      usbvideo1.record_video(name, time)

    name is the file stub of the output video file, which will be
    written into the video folder in the experiment folder and given a
    .avi extension.  time is the length of the video.

    This is a bit clunky, but it hides all of the interactions with
    the computer vision plugin.

    A bit more hands on:

       usbvideo1.start()
       (do some things for a while)
       usbvideo1.stop()
       usbvideo1.save_video(name)

    where name is the same as above.

    '''
    visionfunction3 = Cpt(EpicsSignal, 'CompVisionFunction3')
    _path = Cpt(EpicsSignal, 'FilePath')
    enable = Cpt(EpicsSignal, 'EnableCallbacks')
    framerate = Cpt(EpicsSignal, 'Input1')
    startstop = Cpt(EpicsSignal, 'Input2')

    # def __init__(self):
    #     super().__init__(*args, **kwargs)
    #     self.visionfunction3.put(4)
    #     self.path.put('/nsls2/data3/bmm/assets/usbcam/')
    #     self.enable.put(0)
    #     self.framerate.put(60)
    #     self.startstop.put(0)

    @property
    def path(self):
        this = ''.join(chr(x) for x in self._path.read()['usbvideo1__path']['value'])
        return this[:-1]
    @path.setter
    def path(self, value):
        self._path.put(value)

    def initialize(self):
        self.enable.put(0)
        self.visionfunction3.put(4)
        self.framerate.put(60)
        self.startstop.put(0)
        
    def start(self):
        self.enable.put(1)
        time.sleep(0.5)
        self.startstop.put(1)

    def stop(self):
        self.startstop.put(0)
        time.sleep(0.5)
        self.enable.put(0)

    def find_video(self):
        list_of_files = glob.glob(f'{self.path}/*')
        latest_file = max(list_of_files, key=os.path.getctime)
        return latest_file

    def save_video(self, name='video.avi'):
        folder = os.path.join(user_ns['BMMuser'].folder, 'video')
        if not os.path.isdir(folder):
            os.makedirs(folder)
        shutil.copyfile(os.path.join(self.path, self.find_video()),
                        os.path.join(folder, name))
        report(f'Wrote {os.path.join(folder, name)}', slack=True)
        
    def record_video(self, name='video.avi', time=5):
        folder = os.path.join(user_ns['BMMuser'].folder, 'video')
        if not name.endswith('.avi'):
            name = name + '.avi'
        if os.path.isfile(os.path.join(folder, name)):
            print(f'There is already a file called {name} in your video folder.  Abandoning video.')
            yield from null()
            return()
        action = input(f'\nWrite a {time} second video to {os.path.join(folder, name)}? ' + PROMPTNC)
        if action != '':
            if action[0].lower() == 'n' or action[0].lower() == 'q':
                print('Abandoning video...')
                yield from null()
                return
        yield from mv(self.enable, 1)
        yield from sleep(0.5)
        yield from mv(self.startstop, 1)
        yield from mv(user_ns['busy'], time)
        yield from mv(self.startstop, 0)
        yield from sleep(0.5)
        yield from mv(self.enable, 0)
        print('Waiting 3 seconds to finish writing the video file...')
        yield from mv(user_ns['busy'], 3)
        self.save_video(name)
        print('Done!')
