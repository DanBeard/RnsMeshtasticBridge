import sys
import os
import socket
import argparse
import time
import random
import threading
from meshtastic.protobuf.mesh_pb2 import Data, MeshPacket, Constants, ToRadio, FromRadio
from meshtastic.protobuf.portnums_pb2 import PRIVATE_APP, TEXT_MESSAGE_APP

# Methods for communication with a meshtastic node, since the default pubsub library didn't work on my T3S3

# Configuration constants
MT_MAGIC_0 = 0x94
MT_MAGIC_1 = 0xc3
MESH_DEST_ADDR=0xFFFFFFFF
MESH_SPECIAL_NONCE = 69420 # real mature meshtastic

BUFFER_SIZE = 10000 
MAX_MESH_PACKET = Constants.DATA_PAYLOAD_LEN - 1 

def request_mesh_config_info_packet():
    to_radio = ToRadio()
    to_radio.want_config_id = MESH_SPECIAL_NONCE
    packet = to_radio.SerializeToString()
        
    buflen = len(packet)
    return bytes([MT_MAGIC_0, MT_MAGIC_1, (buflen >> 8) & 0xFF, buflen & 0xFF]) + packet
    

def create_mesh_packet(data, mesh_channel:int, portnum=PRIVATE_APP):
    """
    Create a Meshtastic packet containing the provided data
    Returns a serialized MeshPacket
    """
   
    result = b''
    
    for start_byte in range(0, len(data), MAX_MESH_PACKET):
        to_radio = ToRadio()
        mesh_packet = to_radio.packet
        mesh_packet.decoded.payload = data[start_byte:start_byte+MAX_MESH_PACKET]
        #TODO register a specific private mesh port above this value?
        mesh_packet.decoded.portnum = portnum   #PRIVATE_APP or TEXT_MESSAGE_APP most common
        # Set other required fields
        mesh_packet.to = MESH_DEST_ADDR     # Broadcast
        mesh_packet.id = random.randint(0, 0x7FFFFFFF)  # Generate unique ID
        mesh_packet.channel = mesh_channel
        mesh_packet.want_ack = False

        packet = to_radio.SerializeToString()
        
        buflen = len(packet)
        result+= bytes([MT_MAGIC_0, MT_MAGIC_1, (buflen >> 8) & 0xFF, buflen & 0xFF]) + packet
    
    return result

def decode_mesh_packets(data):
   
    result = []
    idx=0
    while idx < len(data):
        # if we got the magic bytes
        if data[idx] == MT_MAGIC_0 and data[idx + 1] == MT_MAGIC_1:
            # next two bytes are the size
            payload_len = data[idx+2] << 8 | data[idx+3] 
            start_packet_idx = idx+4
            end_packet_idx = start_packet_idx + payload_len
            packet_buf = data[start_packet_idx:end_packet_idx]
            # Extract the payload from the Meshtastic packet
            from_radio = FromRadio.FromString(packet_buf)
            packet = from_radio.packet
            result.append(packet)
            idx = end_packet_idx
        else:
            idx+=1    
            
    return result

class MeshtasticHandle:
    
    def __init__(self, callback, host:str, port:int = 4403, channel:int = 2):
        self.host = host
        self.port = port 
        self.channel = channel
        sock = socket.socket()
        sock.connect((host, port))
        
        # without this it won;t send us anything
        sock.send(request_mesh_config_info_packet())
        self.sock = sock
        
        self._loop = threading.Thread(target=self._recv_loop, args=(callback, ), daemon=True)
        self._loop.start()
        
    def send_text(self, text):
        mesh_packet = create_mesh_packet(text.encode('utf-8'),mesh_channel=self.channel, portnum=TEXT_MESSAGE_APP)
        self.sock.send(mesh_packet)
        
    def send_data(self, text):
        mesh_packet = create_mesh_packet(text.encode('utf-8'),mesh_channel=self.channel, portnum=PRIVATE_APP)
        self.sock.send(mesh_packet)
        
    def _recv_loop(self, callback):
        
        time.sleep(0.5)
        while True:
            data = self.sock.recv(1024)
            if not data:
                return
            
            for packet in decode_mesh_packets(data):
                if packet.HasField('decoded'):
                    # ignore other channels that we aren't using as a bridge
                    if packet.channel == self.channel:
                        # Write the payload directly to stdout
                        callback(packet.decoded.payload.decode("utf-8",errors="backslashreplace"))
                        #print("<--got packet", file=sys.stderr)    
            
        
        
        
        
        