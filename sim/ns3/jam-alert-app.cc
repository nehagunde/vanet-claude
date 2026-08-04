/**
 * jam-alert-app.cc — Implementation of JamAlertApp.
 *
 * OBU behaviour
 * ─────────────
 *   Every BEACON_INTERVAL seconds the OBU reads its current position &
 *   speed from the mobility model and broadcasts a BEACON (or
 *   JAM_DETECTED if speed < JAM_SPEED_THRESHOLD km/h) to 255.255.255.255.
 *
 * RSU behaviour
 * ─────────────
 *   The RSU only listens.  When it sees ≥ JAM_VEH_THRESHOLD vehicles
 *   reporting < 5 km/h within a JAM_TIME_THRESHOLD-second window it
 *   broadcasts a JAM_ALERT once (then resets its counter so it can fire
 *   again if the jam continues).
 *
 * Logging
 * ───────
 *   Every sent/received event is appended to output/alerts.log in the
 *   format:
 *     [T=<sim_s>] NODE=<id> TYPE=<BEACON|JAM_DETECTED|JAM_ALERT>
 *              FROM=<sender> SPEED=<kmh> EDGE=<edge> MSG=<alert>
 */

#include "jam-alert-app.h"

#include "ns3/inet-socket-address.h"
#include "ns3/ipv4-address.h"
#include "ns3/log.h"
#include "ns3/mobility-model.h"
#include "ns3/node.h"
#include "ns3/packet.h"
#include "ns3/simulator.h"
#include "ns3/socket-factory.h"
#include "ns3/udp-socket-factory.h"
#include "ns3/uinteger.h"
#include "ns3/vector.h"

#include <cstring>
#include <iomanip>
#include <sstream>

namespace ns3 {

NS_LOG_COMPONENT_DEFINE("JamAlertApp");
NS_OBJECT_ENSURE_REGISTERED(JamAlertApp);

// ── TypeId ────────────────────────────────────────────────────────────────────

TypeId JamAlertApp::GetTypeId() {
    static TypeId tid = TypeId("ns3::JamAlertApp")
        .SetParent<Application>()
        .SetGroupName("Applications")
        .AddConstructor<JamAlertApp>();
    return tid;
}

JamAlertApp::JamAlertApp()  = default;
JamAlertApp::~JamAlertApp() = default;

// ── Public setup ──────────────────────────────────────────────────────────────

void JamAlertApp::Setup(uint32_t nodeId, bool isRsu,
                        const std::string& logPath, uint16_t port) {
    m_nodeId  = nodeId;
    m_isRsu   = isRsu;
    m_logPath = logPath;
    m_port    = port;
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

void JamAlertApp::StartApplication() {
    // Open (append) the shared log file
    m_log.open(m_logPath, std::ios::app);
    if (!m_log.is_open()) {
        NS_LOG_WARN("JamAlertApp: cannot open log " << m_logPath);
    }

    // Receive socket — bound to the broadcast address on m_port
    TypeId udpTid = UdpSocketFactory::GetTypeId();
    m_rxSocket = Socket::CreateSocket(GetNode(), udpTid);
    InetSocketAddress local(Ipv4Address::GetAny(), m_port);
    m_rxSocket->Bind(local);
    m_rxSocket->SetRecvCallback(MakeCallback(&JamAlertApp::HandleRead, this));
    m_rxSocket->SetAllowBroadcast(true);

    // Transmit socket
    m_txSocket = Socket::CreateSocket(GetNode(), udpTid);
    m_txSocket->SetAllowBroadcast(true);
    m_txSocket->Connect(InetSocketAddress(Ipv4Address("255.255.255.255"), m_port));

    // OBUs beacon; RSUs only listen
    if (!m_isRsu) {
        m_beaconEvent = Simulator::Schedule(
            Seconds(0.0), &JamAlertApp::SendBeacon, this);
    }
}

void JamAlertApp::StopApplication() {
    Simulator::Cancel(m_beaconEvent);
    if (m_rxSocket) { m_rxSocket->Close(); }
    if (m_txSocket) { m_txSocket->Close(); }
    if (m_log.is_open()) { m_log.close(); }
}

// ── Transmission ──────────────────────────────────────────────────────────────

void JamAlertApp::SendBeacon() {
    // Read position from mobility model
    Ptr<MobilityModel> mob = GetNode()->GetObject<MobilityModel>();
    Vector pos = mob ? mob->GetPosition() : Vector(0, 0, 0);

    // Speed placeholder: NS-3 doesn't have live speed data from SUMO.
    // Phase 4 jam_detector.py reads speed_log.json for the real detection.
    // Here we always send BEACON so the 802.11p contact log is populated.
    float speed_kmh = -1.0f;   // -1 = "not available"

    MsgType type = BEACON;  // JAM_DETECTED triggered only when speed injected

    VanetMsg msg{};
    msg.msg_type  = static_cast<uint8_t>(type);
    msg.sender_id = m_nodeId;
    msg.speed_kmh = speed_kmh;
    msg.pos_x     = static_cast<float>(pos.x);
    msg.pos_y     = static_cast<float>(pos.y);
    std::strncpy(msg.edge, "unknown", sizeof(msg.edge) - 1);
    msg.alert_msg[0] = '\0';

    Ptr<Packet> pkt = Create<Packet>(
        reinterpret_cast<const uint8_t*>(&msg), sizeof(msg));
    m_txSocket->Send(pkt);

    std::ostringstream oss;
    oss << "[T=" << std::fixed << std::setprecision(1)
        << Simulator::Now().GetSeconds()
        << "] NODE=" << m_nodeId
        << " SENT=" << (type == BEACON ? "BEACON" : "JAM_DETECTED")
        << " SPEED=" << speed_kmh
        << " X=" << pos.x << " Y=" << pos.y;
    LogEvent(oss.str());

    // Reschedule
    m_beaconEvent = Simulator::Schedule(
        Seconds(BEACON_INTERVAL_S), &JamAlertApp::SendBeacon, this);
}

void JamAlertApp::SendAlert(MsgType type, const std::string& alertMsg) {
    Ptr<MobilityModel> mob = GetNode()->GetObject<MobilityModel>();
    Vector pos = mob ? mob->GetPosition() : Vector(0, 0, 0);

    VanetMsg msg{};
    msg.msg_type  = static_cast<uint8_t>(type);
    msg.sender_id = m_nodeId;
    msg.speed_kmh = 0.0f;
    msg.pos_x     = static_cast<float>(pos.x);
    msg.pos_y     = static_cast<float>(pos.y);
    std::strncpy(msg.edge,      "RSU",        sizeof(msg.edge) - 1);
    std::strncpy(msg.alert_msg, alertMsg.c_str(), sizeof(msg.alert_msg) - 1);

    Ptr<Packet> pkt = Create<Packet>(
        reinterpret_cast<const uint8_t*>(&msg), sizeof(msg));
    m_txSocket->Send(pkt);

    std::ostringstream oss;
    oss << "[T=" << std::fixed << std::setprecision(1)
        << Simulator::Now().GetSeconds()
        << "] NODE=" << m_nodeId
        << " SENT=JAM_ALERT"
        << " MSG=\"" << alertMsg << "\"";
    LogEvent(oss.str());
}

// ── Reception ─────────────────────────────────────────────────────────────────

void JamAlertApp::HandleRead(Ptr<Socket> socket) {
    Ptr<Packet> pkt;
    Address     from;

    while ((pkt = socket->RecvFrom(from)) != nullptr) {
        if (pkt->GetSize() < sizeof(VanetMsg)) {
            continue;
        }

        VanetMsg msg{};
        pkt->CopyData(reinterpret_cast<uint8_t*>(&msg), sizeof(msg));

        // Null-terminate string fields defensively
        msg.edge[31]      = '\0';
        msg.alert_msg[63] = '\0';

        const char* typeStr = "BEACON";
        if (msg.msg_type == JAM_DETECTED) typeStr = "JAM_DETECTED";
        if (msg.msg_type == JAM_ALERT)    typeStr = "JAM_ALERT";

        std::ostringstream oss;
        oss << "[T=" << std::fixed << std::setprecision(1)
            << Simulator::Now().GetSeconds()
            << "] NODE=" << m_nodeId
            << " RECV=" << typeStr
            << " FROM=" << msg.sender_id
            << " SPEED=" << msg.speed_kmh
            << " EDGE="  << msg.edge
            << " MSG=\"" << msg.alert_msg << "\"";
        LogEvent(oss.str());

        // RSU jam-aggregation logic
        if (m_isRsu && msg.msg_type == JAM_DETECTED) {
            double now = Simulator::Now().GetSeconds();

            if (m_slowCount == 0) {
                m_firstSlowAt = now;
            }

            // Only count reports within the time window
            if ((now - m_firstSlowAt) <= JAM_TIME_THRESHOLD) {
                m_slowCount++;
            } else {
                // Window expired — restart
                m_slowCount   = 1;
                m_firstSlowAt = now;
            }

            if (m_slowCount >= JAM_VEH_THRESHOLD) {
                SendAlert(JAM_ALERT,
                    "Traffic jam detected — take U-turn at nearest junction");
                // Reset so we don't spam
                m_slowCount   = 0;
                m_firstSlowAt = 0.0;
            }
        }
    }
}

// ── Logging ───────────────────────────────────────────────────────────────────

void JamAlertApp::LogEvent(const std::string& line) {
    NS_LOG_INFO(line);
    if (m_log.is_open()) {
        m_log << line << "\n";
        m_log.flush();
    }
}

} // namespace ns3
