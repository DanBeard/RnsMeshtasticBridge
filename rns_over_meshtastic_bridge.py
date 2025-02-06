#!/usr/bin/env python3

import sys
import os
import socket
import argparse
import select
import random
from meshtastic.protobuf.mesh_pb2 import Data, MeshPacket, Constants, ToRadio, FromRadio
from meshtastic.protobuf.portnums_pb2 import PRIVATE_APP, TEXT_MESSAGE_APP

# TODO: refactor these to use the utils so it's all in one place
import meshtastic_utils

# Attempt to have meshtastic ask as a transport bus for RNS packets. 
# Current status: works.... kinda. SHortfast is promising, but LongFast times out 9 times out of 10
# Need to evaluate logic. Maybe latency is too high and there's a knob in RNS we can turn?



# Configuration constants
MT_MAGIC_0 = 0x94
MT_MAGIC_1 = 0xc3
MESH_DEST_ADDR=0xFFFFFFFF
MESH_SPECIAL_NONCE = 69420 # real mature meshtastic

BUFFER_SIZE = 10000 
MAX_MESH_PACKET = Constants.DATA_PAYLOAD_LEN - 1 

parser = argparse.ArgumentParser(
                    prog='rns_meshtastic_bridge',
                    description='Pipe interface that acts a bridge for RNS over a meshtastic channel using MEshtastics TCP socket API'
                    )
parser.add_argument('meshtastic_ip')
parser.add_argument("-p",'--port', default=4403, type=int)
parser.add_argument("-c","--channel", default=2, type=int, help="The Meshtastic channel index to use as a bridge. NOTE: this means the mestastic channel as it appears int he app (e.g. LongFast is usually 0). Radio channel doesn't matter ")

def request_mesh_config_info_packet():
    to_radio = ToRadio()
    to_radio.want_config_id = MESH_SPECIAL_NONCE
    packet = to_radio.SerializeToString()
        
    buflen = len(packet)
    return bytes([MT_MAGIC_0, MT_MAGIC_1, (buflen >> 8) & 0xFF, buflen & 0xFF]) + packet
    

def create_mesh_packet(data, mesh_channel:int):
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
        mesh_packet.decoded.portnum = PRIVATE_APP   #TEXT_MESSAGE_APP
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
   
    

def main(mesh_host:str, mesh_port:int, mesh_channel:int):
    try:
        # Create and connect socket
        sock = socket.socket()
        sock.connect((mesh_host, mesh_port))
        sock.setblocking(False)
        #print("Connected to Meshtastic device", file=sys.stderr)
        
        # without this it won;t send us anything
        sock.send(request_mesh_config_info_packet())
        
        # Set stdin to non-blocking
        # got to be files for nonblocking
        stdin = os.fdopen(0, 'rb',0)
        os.set_blocking(stdin.fileno(), False)
        
        stdout = os.fdopen(1, 'wb',0)
        
        # Lists to track input/output sources
        inputs = [stdin, sock]
        outputs = [] #[stdout, sock]  # unused for now. It's simple proxy
        
        while True:
            # Wait for at least one of the sockets to be ready for processing
            # Timeout of 0.1 seconds
            readable, writable, exceptional = select.select(inputs, outputs, inputs + outputs, 60)
            
            for s in readable:
                # handle stdin
                if s is stdin or s== 0:
                    try:
                        data = s.read(BUFFER_SIZE)
                        if data:
                            # Create and send Meshtastic packet
                            mesh_packet = create_mesh_packet(data,mesh_channel=mesh_channel)
                            sock.send(mesh_packet)
                            #print(f"sent_packet->payload len={mesh_packet}", file=sys.stderr)
                        else:
                            print("no data?? STDIN is closed. Exiting", file=sys.stderr)
                            exit(-1)
                    except BlockingIOError as e:
                        print("BlockingIOError->"+str(e), file=sys.stderr)
                        continue
                    except EOFError:
                        print("EOFError->"+str(e), file=sys.stderr)
                        return
                #handle meshtastic tcp in
                elif s is sock:
                    try:
                        data = s.recv(BUFFER_SIZE)
                        if not data:
                            print("Connection closed by remote host", file=sys.stderr)
                            return
                        
                        for packet in decode_mesh_packets(data):
                            if packet.HasField('decoded'):
                                # ignore other channels that we aren't using as a bridge
                                if packet.channel == mesh_channel:
                                    # Write the payload directly to stdout
                                    stdout.write(packet.decoded.payload)
                                    #print("<--got packet", file=sys.stderr)                    
                        stdout.flush()
                    except BlockingIOError:
                        print("BlockingIOError2->"+str(e), file=sys.stderr)
                        continue
                    except ConnectionError:
                        print("Connection error", file=sys.stderr)
                        return
                else:
                    print("not an option?", file=sys.stderr)
            # Handle any exceptional conditions
            for s in exceptional:
                print(f"Exception condition on {s}", file=sys.stderr)
                inputs.remove(s)
                if s is sock:
                    s.close()
                    return
                
    except KeyboardInterrupt:
        print("Shutting down...", file=sys.stderr)
    
    finally:
        sock.close()

if __name__ == "__main__":
    args = parser.parse_args(args=None if sys.argv[1:] else ['--help'])
    
        
    main(args.meshtastic_ip, args.port, args.channel)