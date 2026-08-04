/**
 * vanet-scenario.cc — NS-3 802.11p WAVE simulation for VANET jam detection.
 *
 * Topology
 * ────────
 *   10 OBU nodes  (nodes  0 – 9)  : mobile vehicles, mobility from SUMO
 *    7 RSU nodes  (nodes 10 – 16) : fixed roadside units
 *
 * Inputs  (produced by traci_supervisor.py)
 * ──────
 *   sim/bridge/mobility.ns2      — ns2 waypoint trace for OBUs
 *   sim/bridge/rsu_static.json   — RSU XY positions
 *
 * Outputs
 * ───────
 *   output/alerts.log            — sent/received message log (appended)
 *
 * Radio
 * ─────
 *   IEEE 802.11p / WAVE, 10 MHz channel, 6 Mb/s, CCH 178
 *   Tx power 20 dBm → ~300 m range in free space
 *   Friis propagation loss model
 *
 * Build (inside ns-3 source tree)
 * ────────────────────────────────
 *   cp vanet-scenario.cc jam-alert-app.h jam-alert-app.cc scratch/
 *   ./ns3 build
 *   ./ns3 run "vanet-scenario \
 *       --mobilityFile=/home/kali/vanet_claude/sim/bridge/mobility.ns2 \
 *       --rsuFile=/home/kali/vanet_claude/sim/bridge/rsu_static.json \
 *       --logFile=/home/kali/vanet_claude/output/alerts.log \
 *       --simTime=600"
 */

#include "jam-alert-app.h"

#include "ns3/command-line.h"
#include "ns3/constant-position-mobility-model.h"
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/wifi-module.h"
#include "ns3/yans-wifi-helper.h"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

// ── Simple JSON helpers (no external library needed) ──────────────────────────

struct RsuEntry {
    uint32_t    ns3NodeId;
    std::string rsuId;
    double      x;
    double      y;
};

/**
 * Minimal parser for the flat rsu_static.json array produced by
 * traci_supervisor.py.  Looks for "ns3_node_id", "x_m", "y_m" keys.
 * Returns empty vector on any error.
 */
static std::vector<RsuEntry> LoadRsuJson(const std::string& path) {
    std::vector<RsuEntry> result;
    std::ifstream f(path);
    if (!f.is_open()) {
        std::cerr << "ERROR: cannot open " << path << "\n";
        return result;
    }
    std::string content((std::istreambuf_iterator<char>(f)),
                         std::istreambuf_iterator<char>());

    // Split on '}' to get individual object strings
    std::istringstream ss(content);
    std::string token;
    while (std::getline(ss, token, '}')) {
        auto extract = [&](const std::string& key) -> double {
            auto pos = token.find("\"" + key + "\"");
            if (pos == std::string::npos) return 0.0;
            auto colon = token.find(':', pos);
            if (colon == std::string::npos) return 0.0;
            return std::stod(token.substr(colon + 1));
        };
        auto extractUint = [&](const std::string& key) -> uint32_t {
            return static_cast<uint32_t>(extract(key));
        };
        auto extractStr = [&](const std::string& key) -> std::string {
            auto pos = token.find("\"" + key + "\"");
            if (pos == std::string::npos) return "";
            auto colon = token.find(':', pos);
            auto q1    = token.find('"', colon + 1);
            auto q2    = token.find('"', q1 + 1);
            if (q1 == std::string::npos || q2 == std::string::npos) return "";
            return token.substr(q1 + 1, q2 - q1 - 1);
        };

        if (token.find("ns3_node_id") == std::string::npos) continue;
        RsuEntry e;
        e.ns3NodeId = extractUint("ns3_node_id");
        e.rsuId     = extractStr("rsu_id");
        e.x         = extract("x_m");
        e.y         = extract("y_m");
        result.push_back(e);
    }
    return result;
}

// ── Main ──────────────────────────────────────────────────────────────────────

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("VanetScenario");

int main(int argc, char* argv[]) {
    // ── Command-line defaults ─────────────────────────────────────────────────
    std::string mobilityFile =
        "/home/kali/vanet_claude/sim/bridge/mobility.ns2";
    std::string rsuFile =
        "/home/kali/vanet_claude/sim/bridge/rsu_static.json";
    std::string logFile =
        "/home/kali/vanet_claude/output/alerts.log";
    double simTime = 600.0;
    uint16_t port  = 7777;

    CommandLine cmd(__FILE__);
    cmd.AddValue("mobilityFile", "Path to mobility.ns2",   mobilityFile);
    cmd.AddValue("rsuFile",      "Path to rsu_static.json", rsuFile);
    cmd.AddValue("logFile",      "Path to output alerts.log", logFile);
    cmd.AddValue("simTime",      "Simulation duration (s)", simTime);
    cmd.AddValue("port",         "UDP port for VANET messages", port);
    cmd.Parse(argc, argv);

    // ── Logging ───────────────────────────────────────────────────────────────
    LogComponentEnable("VanetScenario",  LOG_LEVEL_INFO);
    LogComponentEnable("JamAlertApp",    LOG_LEVEL_INFO);

    // ── RSU positions ─────────────────────────────────────────────────────────
    std::vector<RsuEntry> rsus = LoadRsuJson(rsuFile);
    if (rsus.empty()) {
        std::cerr << "ERROR: no RSU entries loaded from " << rsuFile << "\n";
        return 1;
    }
    std::cout << "Loaded " << rsus.size() << " RSUs from " << rsuFile << "\n";

    uint32_t nObu = 10;
    uint32_t nRsu = static_cast<uint32_t>(rsus.size());  // 7

    // ── Create nodes ──────────────────────────────────────────────────────────
    NodeContainer obuNodes, rsuNodes;
    obuNodes.Create(nObu);
    rsuNodes.Create(nRsu);

    NodeContainer allNodes;
    allNodes.Add(obuNodes);
    allNodes.Add(rsuNodes);

    // ── 802.11p radio (no wave module needed) ────────────────────────────────
    YansWifiChannelHelper wifiChannel;
    wifiChannel.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
    wifiChannel.AddPropagationLoss("ns3::FriisPropagationLossModel",
                                   "Frequency", DoubleValue(5.9e9));

    YansWifiPhyHelper wifiPhy;
    wifiPhy.SetChannel(wifiChannel.Create());
    wifiPhy.Set("TxPowerStart", DoubleValue(20.0));  // dBm
    wifiPhy.Set("TxPowerEnd",   DoubleValue(20.0));

    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211p);
    wifi.SetRemoteStationManager(
        "ns3::ConstantRateWifiManager",
        "DataMode",    StringValue("OfdmRate6MbpsBW10MHz"),
        "ControlMode", StringValue("OfdmRate6MbpsBW10MHz"));

    WifiMacHelper wifiMac;
    wifiMac.SetType("ns3::AdhocWifiMac");

    NetDeviceContainer devices = wifi.Install(wifiPhy, wifiMac, allNodes);

    // ── Internet stack (needed for UDP sockets) ───────────────────────────────
    InternetStackHelper internet;
    internet.Install(allNodes);

    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.1.0.0", "255.255.0.0");
    Ipv4InterfaceContainer ifaces = ipv4.Assign(devices);

    // ── OBU mobility — ns2 waypoint trace ────────────────────────────────────
    Ns2MobilityHelper ns2mob(mobilityFile);
    ns2mob.Install(obuNodes.Begin(), obuNodes.End());

    // Assign a fallback mobility model for any OBU not covered by the trace
    // (e.g. a vehicle that never departed in SUMO).  Without this, YansWifiChannel
    // crashes dereferencing a null mobility pointer.
    for (uint32_t i = 0; i < nObu; ++i) {
        if (!obuNodes.Get(i)->GetObject<MobilityModel>()) {
            Ptr<ConstantPositionMobilityModel> dummy =
                CreateObject<ConstantPositionMobilityModel>();
            dummy->SetPosition(Vector(-9999.0, -9999.0, 0.0));
            obuNodes.Get(i)->AggregateObject(dummy);
            NS_LOG_INFO("OBU node " << i << " has no trace entry — parked off-network");
        }
    }

    // ── RSU mobility — fixed positions ───────────────────────────────────────
    for (uint32_t i = 0; i < nRsu; ++i) {
        Ptr<ConstantPositionMobilityModel> mob =
            CreateObject<ConstantPositionMobilityModel>();
        mob->SetPosition(Vector(rsus[i].x, rsus[i].y, 1.5));  // 1.5 m height
        rsuNodes.Get(i)->AggregateObject(mob);
    }

    // ── Ensure output directory exists ────────────────────────────────────────
    {
        // Extract directory from logFile path
        auto slash = logFile.rfind('/');
        if (slash != std::string::npos) {
            std::string dir = logFile.substr(0, slash);
            std::string cmd_mkdir = "mkdir -p \"" + dir + "\"";
            std::system(cmd_mkdir.c_str());
        }
    }

    // ── Install JamAlertApp on all nodes ─────────────────────────────────────
    ApplicationContainer apps;

    // OBUs: nodes 0–9
    for (uint32_t i = 0; i < nObu; ++i) {
        Ptr<JamAlertApp> app = CreateObject<JamAlertApp>();
        app->Setup(i, /*isRsu=*/false, logFile, port);
        obuNodes.Get(i)->AddApplication(app);
        app->SetStartTime(Seconds(0.0));
        app->SetStopTime(Seconds(simTime));
        apps.Add(app);
    }

    // RSUs: nodes 10–16
    for (uint32_t i = 0; i < nRsu; ++i) {
        Ptr<JamAlertApp> app = CreateObject<JamAlertApp>();
        app->Setup(10 + i, /*isRsu=*/true, logFile, port);
        rsuNodes.Get(i)->AddApplication(app);
        app->SetStartTime(Seconds(0.0));
        app->SetStopTime(Seconds(simTime));
        apps.Add(app);
    }

    // ── Run ───────────────────────────────────────────────────────────────────
    std::cout << "Starting NS-3 simulation for " << simTime << " s\n";
    std::cout << "  OBU nodes : 0 – " << (nObu - 1) << "\n";
    std::cout << "  RSU nodes : 10 – " << (10 + nRsu - 1) << "\n";
    std::cout << "  Log       : " << logFile << "\n\n";

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();
    Simulator::Destroy();

    std::cout << "\nNS-3 simulation complete.\n";
    std::cout << "  Alerts log → " << logFile << "\n";
    return 0;
}
