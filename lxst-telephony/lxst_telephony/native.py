"""Native audio helpers for LXST telephony."""

from LXST.Primitives.Telephony import Telephone


class NativeAudio:
    def __init__(self, speaker=None, microphone=None, ringer=None,
                 low_latency=False, agc=True, bandpass=True,
                 echo_cancellation=True):
        self.speaker = speaker
        self.microphone = microphone
        self.ringer = ringer
        self.low_latency = low_latency
        self.agc = agc
        self.bandpass = bandpass
        self.echo_cancellation = echo_cancellation

    def configure(self, telephone):
        if self.speaker is not None:
            telephone.set_speaker(self.speaker)
        if self.microphone is not None:
            telephone.set_microphone(self.microphone)
        if self.ringer is not None:
            telephone.set_ringer(self.ringer)
        telephone.set_low_latency_output(self.low_latency)
        telephone.enable_agc(self.agc)
        telephone.enable_bandpass(self.bandpass)
        telephone.enable_echo_cancellation(self.echo_cancellation)

    @staticmethod
    def inputs():
        return Telephone.available_inputs()

    @staticmethod
    def outputs():
        return Telephone.available_outputs()

    @staticmethod
    def default_input():
        return Telephone.default_input()

    @staticmethod
    def default_output():
        return Telephone.default_output()
