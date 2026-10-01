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

#include <cmath>
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
                        const std::string& logPath, uint16_t port,
                        const std::string& alertMsg) {
    m_nodeId   = nodeId;
    m_isRsu    = isRsu;
    m_logPath  = logPath;
    m_port     = port;
    m_alertMsg = alertMsg;
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
    Ptr<MobilityModel> mob = GetNode()->GetObject<MobilityModel>();
    Vector pos = mob ? mob->GetPosition() : Vector(0, 0, 0);

    // Real speed from the ns2 waypoint mobility model velocity vector
    float speed_kmh = 0.0f;
    if (mob) {
        Vector vel = mob->GetVelocity();
        double speed_ms = std::sqrt(vel.x * vel.x + vel.y * vel.y);
        speed_kmh = static_cast<float>(speed_ms * 3.6);
    }

    // Count consecutive 1-second intervals below jam threshold
    if (speed_kmh < JAM_SPEED_THRESHOLD) {
        m_slowSeconds++;
    } else {
        m_slowSeconds = 0;
    }

    // Upgrade to JAM_DETECTED once the vehicle has been slow for > 30 s
    MsgType type = (m_slowSeconds > static_cast<uint32_t>(JAM_TIME_THRESHOLD))
                   ? JAM_DETECTED : BEACON;

    VanetMsg msg{};
    msg.msg_type  = static_cast<uint8_t>(type);
    msg.sender_id = m_nodeId;
    msg.speed_kmh = speed_kmh;
    msg.pos_x     = static_cast<float>(pos.x);
    msg.pos_y     = static_cast<float>(pos.y);
    std::strncpy(msg.edge, "v2i", sizeof(msg.edge) - 1);
    msg.alert_msg[0] = '\0';

    Ptr<Packet> pkt = Create<Packet>(
        reinterpret_cast<const uint8_t*>(&msg), sizeof(msg));
    m_txSocket->Send(pkt);

    std::ostringstream oss;
    oss << "[T=" << std::fixed << std::setprecision(1)
        << Simulator::Now().GetSeconds()
        << "] NODE=" << m_nodeId
        << " SENT=" << (type == BEACON ? "BEACON" : "JAM_DETECTED")
        << " SPEED=" << std::fixed << std::setprecision(2) << speed_kmh
        << " X=" << std::fixed << std::setprecision(1) << pos.x
        << " Y=" << pos.y
        << " SLOW_S=" << m_slowSeconds;
    LogEvent(oss.str());

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

        // RSU jam-aggregation logic — count DISTINCT vehicles in 30-s window
        if (m_isRsu && msg.msg_type == JAM_DETECTED) {
            double now = Simulator::Now().GetSeconds();

            // Window expired — reset
            if (m_firstSlowAt > 0.0 && (now - m_firstSlowAt) > JAM_TIME_THRESHOLD) {
                m_seenSenders.clear();
                m_firstSlowAt = 0.0;
                m_jamFired    = false;
            }

            if (m_firstSlowAt == 0.0) {
                m_firstSlowAt = now;
            }

            m_seenSenders.insert(msg.sender_id);

            // Log every JAM_DETECTED received at RSU
            std::ostringstream rlog;
            rlog << "[T=" << std::fixed << std::setprecision(1) << now
                 << "] RSU=" << m_nodeId
                 << " JAM_DETECTED_FROM=" << msg.sender_id
                 << " SPEED=" << std::fixed << std::setprecision(2) << msg.speed_kmh
                 << " DISTINCT_COUNT=" << m_seenSenders.size();
            LogEvent(rlog.str());

            if (!m_jamFired && m_seenSenders.size() >= JAM_VEH_THRESHOLD) {
                m_jamFired = true;

                // Log QUORUM_REACHED with the vehicle list
                std::ostringstream qlog;
                qlog << "[T=" << std::fixed << std::setprecision(1) << now
                     << "] RSU=" << m_nodeId
                     << " QUORUM_REACHED vehicles=[";
                bool first = true;
                for (uint32_t id : m_seenSenders) {
                    if (!first) qlog << ",";
                    qlog << id;
                    first = false;
                }
                qlog << "]";
                LogEvent(qlog.str());

                std::string alertStr = m_alertMsg.empty()
                    ? "Take alternate route: jam detected ahead"
                    : m_alertMsg;
                SendAlert(JAM_ALERT, alertStr);
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
