/**
 * jam-alert-app.h — VANET WAVE application for jam detection & alerting.
 *
 * Each OBU/RSU node runs one instance of JamAlertApp:
 *   - OBUs broadcast a beacon every BEACON_INTERVAL seconds.
 *   - RSUs listen, aggregate, and rebroadcast JAM_ALERT if jam confirmed.
 *   - All received packets are logged to output/alerts.log.
 *
 * Message types (packed into a single UDP payload):
 *   BEACON        — periodic hello, carries speed & edge info
 *   JAM_DETECTED  — OBU self-reports slow speed (< 5 km/h)
 *   JAM_ALERT     — RSU rebroadcast after threshold reached
 */

#ifndef JAM_ALERT_APP_H
#define JAM_ALERT_APP_H

#include "ns3/application.h"
#include "ns3/event-id.h"
#include "ns3/ipv4-address.h"
#include "ns3/ptr.h"
#include "ns3/socket.h"
#include "ns3/traced-callback.h"

#include <cstdint>
#include <fstream>
#include <set>
#include <string>

namespace ns3 {

// ── Message types ─────────────────────────────────────────────────────────────

enum MsgType : uint8_t {
    BEACON       = 0,
    JAM_DETECTED = 1,
    JAM_ALERT    = 2,
};

// ── Wire format (fixed-size, no padding) ──────────────────────────────────────

#pragma pack(push, 1)
struct VanetMsg {
    uint8_t  msg_type;       // MsgType
    uint32_t sender_id;      // NS-3 node index
    float    speed_kmh;      // current speed (OBU only; 0 for RSU)
    float    pos_x;          // SUMO x coordinate (metres)
    float    pos_y;          // SUMO y coordinate (metres)
    char     edge[32];       // SUMO road-edge ID (null-terminated)
    char     alert_msg[64];  // human-readable alert (JAM_ALERT only)
};
#pragma pack(pop)

// ── Application class ─────────────────────────────────────────────────────────

class JamAlertApp : public Application {
public:
    static TypeId GetTypeId();

    JamAlertApp();
    ~JamAlertApp() override;

    /**
     * Configure the node before simulation start.
     *
     * @param nodeId     NS-3 node index (0-9 OBU, 10-16 RSU)
     * @param isRsu      true → RSU mode (listens, re-alerts; doesn't beacon)
     * @param logPath    path to output/v2/alerts.log (shared by all nodes)
     * @param port       UDP port (default 7777)
     */
    void Setup(uint32_t nodeId, bool isRsu,
               const std::string& logPath, uint16_t port = 7777,
               const std::string& alertMsg = "");

    // Phase 3: point this RSU at its wired backhaul peer (the upstream RSU
    // toward approaching vehicles).  Called from vanet-scenario.cc after
    // the PointToPoint links are assigned IPs.
    void SetBackhaulPeer(Ipv4Address peerAddr, uint16_t bkPort = 7778);

private:
    // Application lifecycle
    void StartApplication() override;
    void StopApplication()  override;

    // Transmission
    void SendBeacon();
    void SendAlert(MsgType type, const std::string& alertMsg = "");

    // Reception
    void HandleRead(Ptr<Socket> socket);

    // Phase 3: wired backhaul relay
    void HandleBackhaulRead(Ptr<Socket> socket);
    void SendBackhaulAlert(const std::string& alertMsg);

    // Helpers
    void LogEvent(const std::string& line);

    // State
    uint32_t    m_nodeId   {0};
    bool        m_isRsu    {false};
    uint16_t    m_port     {7777};
    std::string m_logPath;
    std::string m_alertMsg;

    Ptr<Socket>  m_rxSocket;         // 802.11p receive socket (port 7777)
    Ptr<Socket>  m_txSocket;         // 802.11p send socket
    EventId      m_beaconEvent;      // periodic beacon timer
    std::ofstream m_log;             // shared log file (append)

    // OBU-side: consecutive beacon intervals below speed threshold
    uint32_t m_slowSeconds {0};

    // RSU-side: distinct OBU senders in current 30-second window
    std::set<uint32_t> m_seenSenders;
    double   m_firstSlowAt {0.0};
    bool     m_jamFired    {false};

    // Phase 3: wired backhaul to upstream (approach-side) RSU
    bool         m_hasBkPeer  {false};
    Ipv4Address  m_bkPeerAddr;
    uint16_t     m_bkPort     {7778};
    Ptr<Socket>  m_bkTxSocket;       // unicast to peer RSU
    Ptr<Socket>  m_bkRxSocket;       // listen for relay from jam-side RSU

    // Packet counters [0=BEACON, 1=JAM_DETECTED, 2=JAM_ALERT]
    uint64_t m_cntSent[3] {0, 0, 0};
    uint64_t m_cntRecv[3] {0, 0, 0};

    static constexpr double   BEACON_INTERVAL_S   = 1.0;
    static constexpr float    JAM_SPEED_THRESHOLD = 5.0f;
    static constexpr uint32_t JAM_VEH_THRESHOLD   = 3;
    static constexpr double   JAM_TIME_THRESHOLD  = 30.0;
};

} // namespace ns3

#endif  // JAM_ALERT_APP_H
