# MIT License - Copyright (c) 2024 Mark Qvist / unsigned.io

# This example illustrates creating a custom interface
# definition, that can be loaded and used by Reticulum at
# runtime. Any number of custom interfaces can be created
# and loaded. To use the interface place it in the folder
# ~/.reticulum/interfaces, and add an interface entry to
# your Reticulum configuration file similar to this:

#  [[Example Custom Interface]]
#    type = ExampleInterface
#    enabled = no
#    mode = gateway
#    port = /dev/ttyUSB0
#    speed = 115200
#    databits = 8
#    parity = none
#    stopbits = 1

from time import sleep
import RNS
import sys
import threading
import time
import socket
import random

from RNS.Interfaces.Interface import Interface

# Configuration constants
MT_MAGIC_0 = 0x94
MT_MAGIC_1 = 0xc3
MESH_DEST_ADDR=0xFFFFFFFF
MESH_SPECIAL_NONCE = 69420 # real mature meshtastic


# Let's define our custom interface class. It must
# be a sub-class of the RNS "Interface" class.
class MeshtasticInterface(Interface):
    # All interface classes must define a default
    # IFAC size, used in IFAC setup when the user
    # has not specified a custom IFAC size. This
    # option is specified in bytes.
    DEFAULT_IFAC_SIZE = 8

    # The following properties are local to this
    # particular interface implementation.
    host    = None
    port     = None
    channel  = None


    # All Reticulum interfaces must have an __init__
    # method that takes 2 positional arguments:
    # The owner RNS Transport instance, and a dict
    # of configuration values.
    def __init__(self, owner, configuration):

        # The following lines demonstrate handling
        # potential dependencies required for the
        # interface to function correctly.
        import importlib
        if importlib.util.find_spec('meshtastic') != None:
            import meshtastic
            from meshtastic.protobuf.portnums_pb2 import PRIVATE_APP
        else:
            RNS.log("Using this interface requires a meshtastic module to be installed.", RNS.LOG_CRITICAL)
            RNS.log("You can install one with the command: python3 -m pip install meshtastic", RNS.LOG_CRITICAL)
            RNS.panic()

        # We start out by initialising the super-class
        super().__init__()
        self._recvbuf = bytes()
        self._recv_partial_msg = []

        # To make sure the configuration data is in the
        # correct format, we parse it through the following
        # method on the generic Interface class. This step
        # is required to ensure compatibility on all the
        # platforms that Reticulum supports.
        ifconf    = Interface.get_config_obj(configuration)

        # Read the interface name from the configuration
        # and set it on our interface instance.
        name      = ifconf["name"]
        self.name = name

        # We read configuration parameters from the supplied
        # configuration data, and provide default values in
        # case any are missing.
        host      = ifconf["host"] if "host" in ifconf else None
        port      = int(ifconf["port"]) if "port" in ifconf else 4403
        channel     = int(ifconf["channel"]) if "channel" in ifconf else None
        
        # In case no port is specified, we abort setup by
        # raising an exception.
        if host == None:
            raise ValueError(f"No host specified for {self}")
        
        if port == None:
            raise ValueError(f"No port specified for {self}")
        
        if channel == None:
            raise ValueError(f"No channel specified for {self}")
        
        self.host = host
        self.channel = channel
        self.port = port

        # All interfaces must supply a hardware MTU value
        # to the RNS Transport instance. This value should
        # be the maximum data packet payload size that the
        # underlying medium is capable of handling in all
        # cases without any segmentation.
        self.HW_MTU = meshtastic.protobuf.mesh_pb2.Constants.DATA_PAYLOAD_LEN - 10
        self.owner = owner
        self.mesh_port_num = PRIVATE_APP
        
        # We initially set the "online" property to false,
        # since the interface has not actually been fully
        # initialised and connected yet.
        self.online   = False

        
        # Configure internal properties on the interface
        # according to the supplied configuration.
        self.timeout  = 1000
        self.bitrate = 9000

        # Since all required parameters are now configured,
        # we will try opening the TCP port.
        try:
            self.open_port()
        except Exception as e:
            RNS.log("Could not open TCP port for interface "+str(self), RNS.LOG_ERROR)
            raise e

        self.configure_device()

    def open_port(self):
        RNS.log("Opening meshtastic TCP port "+self.host+":"+str(self.port)+"...", RNS.LOG_VERBOSE)
        # Create and connect socket
        sock = socket.socket()
        sock.connect((self.host, self.port))
        #print("Connected to Meshtastic device", file=sys.stderr)
        
        # without this it won't send us anything
        sock.send(self._request_mesh_config_info_packet())
        self._sock = sock
        
    def _request_mesh_config_info_packet(self):
        from meshtastic.protobuf.mesh_pb2 import ToRadio
        to_radio = ToRadio()
        to_radio.want_config_id = MESH_SPECIAL_NONCE
        packet = to_radio.SerializeToString()
            
        buflen = len(packet)
        return bytes([MT_MAGIC_0, MT_MAGIC_1, (buflen >> 8) & 0xFF, buflen & 0xFF]) + packet
    
    # The only thing required after opening the port
    # is to wait a small amount of time for the
    # hardware to initialise and then start a thread
    # that reads any incoming data from the device.
    def configure_device(self):
        sleep(0.5)
        thread = threading.Thread(target=self.read_loop)
        thread.daemon = True
        thread.start()
        self.online = True
        RNS.log("Meshtastic TCP port "+self.host+":"+str(self.port)+" is now open", RNS.LOG_VERBOSE)


    # This method will be called from our read-loop
    # whenever a full packet has been received over
    # the underlying medium.
    def process_incoming(self, data):
        # Update our received bytes counter
        self.rxb += len(data)            

        # And send the data packet to the Transport
        # instance for processing.
        self.owner.inbound(data, self)

    # Create a Meshtastic packet containing the provided data
    # Returns a serialized MeshPacket
    def _create_mesh_packets(self, data):
        from meshtastic.protobuf.mesh_pb2 import ToRadio, Constants
        
        result = []
        MAX_LEN = Constants.DATA_PAYLOAD_LEN - 4
        for start_byte in range(0, len(data), MAX_LEN):
            to_radio = ToRadio()
            mesh_packet = to_radio.packet
            mesh_packet.decoded.payload = data[start_byte:start_byte+MAX_LEN]
            #TODO register a specific private mesh port above this value?
            mesh_packet.decoded.portnum = self.mesh_port_num   #TEXT_MESSAGE_APP
            # Set other required fields
            mesh_packet.to = MESH_DEST_ADDR     # Broadcast
            mesh_packet.id = 0 # id always 0 for no-ack random.randint(0, 0x7FFFFFFF)  # Generate unique ID
            mesh_packet.channel = self.channel
            mesh_packet.want_ack = False
            
            # increment the portnum of the last packet in the series to indicate we're finished with this reticulum packet
            # TODO: THis is efficient but probably not what meshtatsic had in mind.  A better way?
            if start_byte+MAX_LEN >= len(data):
                mesh_packet.decoded.portnum+=1
            
            packet = to_radio.SerializeToString()
            
            buflen = len(packet)
            result.append(bytes([MT_MAGIC_0, MT_MAGIC_1, (buflen >> 8) & 0xFF, buflen & 0xFF]) + packet)
    
        
        
            
        return result
    
    def _decode_mesh_packets(self,data):
        from meshtastic.protobuf.mesh_pb2 import FromRadio
        # add it to our buffer
        self._recvbuf += data
        # eat through recvbuf until we get MT_MAGIC_0
        first_magic_idx = self._recvbuf.find(MT_MAGIC_0)
        if first_magic_idx < 0:
            self._recvbuf = b''
            return []
            
        elif first_magic_idx > 0:
            self._recvbuf = self._recvbuf[first_magic_idx:]
        
        result = []
        # +4 because a data packet is AT LEAST 4 bytes long
        while len(self._recvbuf) > 4:
            # if we got the magic bytes
            if self._recvbuf[0] == MT_MAGIC_0 and self._recvbuf[1] == MT_MAGIC_1:
                # next two bytes are the size
                payload_len = self._recvbuf[2] << 8 | self._recvbuf[3] 
                start_packet_idx = 4
                end_packet_idx = start_packet_idx + payload_len
                # if we don't have the full packer, then we're done, wait until later
                if end_packet_idx > len(self._recvbuf):
                    return result
                
                packet_buf = self._recvbuf[start_packet_idx:end_packet_idx]
                # Extract the payload from the Meshtastic packet
                from_radio = FromRadio.FromString(packet_buf)
                packet = from_radio.packet
                result.append(packet)
                # cut the packet off from the current buff
                self._recvbuf = self._recvbuf[end_packet_idx:]
            else:
                self._recvbuf = self._recvbuf[1:] #move it up one    
                
        return result
    # The running Reticulum Transport instance will
    # call this method on the interface whenever the
    # interface must transmit a packet.
    def process_outgoing(self,data):
        if self.online:
            for packet in self._create_mesh_packets(data):
                self.txb += self._sock.send(packet)
    
                   
    # This read loop runs in a thread and continously
    # receives bytes from the underlying serial port.
    # When a full packet has been received, it will
    # be sent to the process_incoming methed, which
    # will in turn pass it to the Transport instance.
    def read_loop(self):
        while True:
            sleep(0.05) # give some time, we slow 
            buf = self._sock.recv(2048)
            
            # 0 len means socket broke
            if len(buf) == 0:
                break
            
            packets = self._decode_mesh_packets(buf)
            RNS.log("Got buf of len="+str(len(buf))+" num packets="+str(len(packets)), RNS.LOG_VERBOSE)
            for packet in packets:
                if packet.HasField('decoded'):
                    # ignore other channels that we aren't using as a bridge
                    if packet.channel == self.channel:
                        #add it to our list of recv messages
                        self._recv_partial_msg.append(packet)
                        # if it's portnum is PRIVATE_APP+1, then we know it's the end, so send them all up for processing
                        if packet.decoded.portnum == self.mesh_port_num + 1:
                            r_packet = b''.join(x.decoded.payload for x in self._recv_partial_msg)
                            self.process_incoming(r_packet)
                            self._recv_partial_msg = [] # clear buffer
                
        # something broke, we need to reconnect
        self.online = False
        self.reconnect_port()

    # This method handles serial port disconnects.
    def reconnect_port(self):
        while not self.online:
            try:
                time.sleep(5)
                RNS.log("Attempting to reconnect TCP port "+self.host+":"+str(self.port)+"...", RNS.LOG_VERBOSE)
                self.open_port()
                self.configure_device()
            except Exception as e:
                RNS.log("Error while reconnecting port, the contained exception was: "+str(e), RNS.LOG_ERROR)

        RNS.log("Reconnected serial port for "+str(self))

    # Signal to Reticulum that this interface should
    # not perform any ingress limiting.
    def should_ingress_limit(self):
        return False

    # We must provide a string representation of this
    # interface, that is used whenever the interface
    # is printed in logs or external programs.
    def __str__(self):
        return "MeshtasticInterface["+self.name+"]"

# Finally, register the defined interface class as the
# target class for Reticulum to use as an interface
interface_class = MeshtasticInterface