import LXMF
import RNS
import time
from meshtastic_utils import MeshtasticHandle

# Text bridge that will take Meshtastic text messages from a channel and proxy them as LXMF messages
# TODO: Save Ident so we're not burning a new one every time we boot
# TODO: enable other idents to subscribe instead of hardcoding them
# TODO: Add command line args instead of hardcoding them

# Hey look, It's me. If you see this shoot me a message!
recipient_hexhash = "ad1f5bb0c0d454b52f02a11df3999feb"
recipient_hash = bytes.fromhex(recipient_hexhash)
meshtastic_ip = "10.0.0.235"
meshtastic_port = 4403
meshtastic_channel_idx = 2
MAX_ROUTE_TIMEOUT_MINS = 60 

class RnsMeshtasticBridge:
    
    router = None
    m_connected = False
    
    def  __init__(self):
        self.m_handle = MeshtasticHandle(self.on_meshtastic_text, meshtastic_ip, meshtastic_port, meshtastic_channel_idx)
            
        self.init_rns(recipient_hexhash)
        
        
    def on_meshtastic_text(self, text):
        print(f"Received: {text}")
        if self.router is not None:
            lxm = LXMF.LXMessage(self.r_dest, self.source, str(text),
                            "Meshtastic msg",
                            desired_method=LXMF.LXMessage.OPPORTUNISTIC, include_ticket=True)
    
            self.router.handle_outbound(lxm)
        else:
            print("Can't send. rns router is empty")
            
    
    def r_announce(self):
        self.router.announce(self.source.hash)
        RNS.log("Source announced")
          
    def init_rns(self, recipient_hexhash, display_name="Meshtastic Bridge"):
        # No interface needed since we assume rns is running on localhost
        # or the auto iface should work fine
        
        self.r = RNS.Reticulum()
        router = LXMF.LXMRouter(storagepath="./tmp2")
        self.router = router
        router.register_delivery_callback(self.on_rns_recv)
        self.ident = RNS.Identity()
        self.source = router.register_delivery_identity(self.ident, display_name=display_name)
        self.router.announce(self.source.hash)
        recipient_hash = bytes.fromhex(recipient_hexhash)

        timeout = 0
        while not RNS.Transport.has_path(recipient_hash) and timeout/10 < MAX_ROUTE_TIMEOUT_MINS:
            RNS.log("Destination is not yet known. Requesting path and waiting for announce to arrive...")
            RNS.Transport.request_path(recipient_hash)
            time.sleep(60/10)
            

        # Recall the server identity
        recipient_identity = RNS.Identity.recall(recipient_hash)

        self.r_dest = RNS.Destination(recipient_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
        
        lxm = LXMF.LXMessage(self.r_dest, self.source, "Meshtastic bridge online",
                                "Meshtastic bridge",
                                desired_method=LXMF.LXMessage.OPPORTUNISTIC, include_ticket=True)
        
        router.handle_outbound(lxm)
        
        
    def on_rns_recv(self, message):
        time_string      = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(message.timestamp))
        signature_string = "Signature is invalid, reason undetermined"
        if message.signature_validated:
            signature_string = "Validated"
        else:
            if message.unverified_reason == LXMF.LXMessage.SIGNATURE_INVALID:
                signature_string = "Invalid signature"
            if message.unverified_reason == LXMF.LXMessage.SOURCE_UNKNOWN:
                signature_string = "Cannot verify, source is unknown"

        if message.stamp_valid:
            stamp_string = "Validated"
        else:
            stamp_string = "Invalid"

        # send over meshtastic
        #self.m_interface.sendText(str(message.content_as_string()), channelIndex=self.m_channel_idx)
        self.m_handle.send_text(str(message.content_as_string()))
        
        RNS.log("\t+--- LXMF Delivery ---------------------------------------------")
        RNS.log("\t| Source hash            : "+RNS.prettyhexrep(message.source_hash))
        RNS.log("\t| Source instance        : "+str(message.get_source()))
        RNS.log("\t| Destination hash       : "+RNS.prettyhexrep(message.destination_hash))
        RNS.log("\t| Destination instance   : "+str(message.get_destination()))
        RNS.log("\t| Transport Encryption   : "+str(message.transport_encryption))
        RNS.log("\t| Timestamp              : "+time_string)
        RNS.log("\t| Title                  : "+str(message.title_as_string()))
        RNS.log("\t| Content                : "+str(message.content_as_string()))
        RNS.log("\t| Fields                 : "+str(message.fields))
        if message.ratchet_id:
            RNS.log("\t| Ratchet                : "+str(RNS.Identity._get_ratchet_id(message.ratchet_id)))
        RNS.log("\t| Message signature      : "+signature_string)
        RNS.log("\t| Stamp                  : "+stamp_string)
        RNS.log("\t+---------------------------------------------------------------")
            
    
        
if __name__ == "__main__":
    bridge = RnsMeshtasticBridge()
    time.sleep(0.5) # initial delay before announce to settle
    while True:
        # busy loop while we work
        bridge.r_announce() # announce ourself to the rns
        time.sleep(30*60)