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
     * @param logPath    path to output/alerts.log (shared by all nodes)
     * @param port       UDP port (default 7777)
     */
    void Setup(uint32_t nodeId, bool isRsu,
               const std::string& logPath, uint16_t port = 7777);

private:
    // Application lifecycle
    void StartApplication() override;
    void StopApplication()  override;

    // Transmission
    void SendBeacon();
    void SendAlert(MsgType type, const std::string& alertMsg = "");

    // Reception
    void HandleRead(Ptr<Socket> socket);

    // Helpers
    void LogEvent(const std::string& line);

    // State
    uint32_t    m_nodeId  {0};
    bool        m_isRsu   {false};
    uint16_t    m_port    {7777};
    std::string m_logPath;

    Ptr<Socket>  m_rxSocket;         // bound receive socket
    Ptr<Socket>  m_txSocket;         // send socket
    EventId      m_beaconEvent;      // periodic beacon timer
    std::ofstream m_log;             // shared log file (append)

    // JAM detection counters (RSU-side)
    uint32_t m_slowCount    {0};     // vehicles reporting < 5 km/h
    double   m_firstSlowAt  {0.0};   // simulation time of first slow report

    static constexpr double BEACON_INTERVAL_S   = 1.0;   // beacon every 1 s
    static constexpr float  JAM_SPEED_THRESHOLD = 5.0f;  // km/h
    static constexpr uint32_t JAM_VEH_THRESHOLD = 3;     // ≥ 3 vehicles
    static constexpr double   JAM_TIME_THRESHOLD = 30.0; // seconds
};

} // namespace ns3

#endif  // JAM_ALERT_APP_H
