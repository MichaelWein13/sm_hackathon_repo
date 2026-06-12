import React, { useState, useEffect, useMemo, useRef } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { forceCollide } from 'd3-force';

import { fetchGraphData, fetchInsights } from './Api'; // Adjust casing to match your file

const GRAPH_POLL_INTERVAL_MS = 20000;

export default function App() {
  const [rawData, setRawData] = useState(null);
  const [insights, setInsights] = useState(null);
  const [loading, setLoading] = useState(true);
  const [graphSource, setGraphSource] = useState('static');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [selectedAlertZone, setSelectedAlertZone] = useState(null);
  const [highlightedAlertZone, setHighlightedAlertZone] = useState(null);
  const fgRef = useRef(); // 1. Reference to the graph to tweak its physics
  const alertRefs = useRef({});
  const highlightTimeoutRef = useRef(null);

  // Fetch data
  useEffect(() => {
    let timerId;
    let active = true;

    const loadGraph = () => {
      if (!active) return;
      if (graphSource === 'static') {
        setLoading(true);
      }

      fetchGraphData(graphSource)
        .then(graphResponse => {
          if (!active) return;
          setRawData(graphResponse);
          setLoading(false);
        })
        .catch(err => {
          if (!active) return;
          console.error("Failed to load graph data:", err);
          setRawData(null);
          setLoading(false);
        })
        .finally(() => {
          if (!active) return;
          timerId = window.setTimeout(loadGraph, GRAPH_POLL_INTERVAL_MS);
        });
    };

    loadGraph();

    return () => {
      active = false;
      if (timerId) {
        window.clearTimeout(timerId);
      }
    };
  }, [graphSource]);

  useEffect(() => {
    fetchInsights(graphSource)
      .then(insightResponse => setInsights(insightResponse))
      .catch(err => {
        console.error('Failed to load insights:', err);
      });
  }, [graphSource]);

  // Subscribe to insight updates over SSE.
  useEffect(() => {
    if (!window.EventSource) {
      console.warn('SSE not supported by this browser');
      return;
    }

    const source = new EventSource('/analytics/insights');

    source.addEventListener('open', () => {
      console.log('[Insights SSE] connected');
    });

    source.addEventListener('message', event => {
      try {
        const update = JSON.parse(event.data);
        if (update && typeof update === 'object') {
          setInsights(update);
        }
      } catch (err) {
        console.error('[Insights SSE] parse error:', err);
      }
    });

    source.addEventListener('error', err => {
      if (source.readyState === EventSource.CLOSED) {
        console.warn('[Insights SSE] connection closed');
      } else {
        console.error('[Insights SSE] error:', err);
      }
    });

    return () => {
      source.close();
    };
  }, []);

  const severityColorMap = {
    detecting: '#95a5a6',
    warning: '#f1c40f',
    critical: '#e74c3c',
    resolving: '#5dade2'
  };

  const parseNumber = value => {
    const n = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(n) ? n : 0;
  };

  const safeGet = (obj, key) => {
    if (!obj) return undefined;
    return obj[key] ?? obj[` ${key} `] ?? obj[key.trim()];
  };

  const getEdgeMetric = (edge, field) => parseNumber(safeGet(edge, field));

  const assignInitialLayout = (nodes, links, layoutDimensions) => {
    if (!layoutDimensions || !layoutDimensions.width || !layoutDimensions.height) return;
    const centerX = layoutDimensions.width / 2;
    const centerY = layoutDimensions.height / 2;
    const adjacency = new Map();
    const degree = new Map();

    nodes.forEach(node => {
      adjacency.set(node.id, new Set());
      degree.set(node.id, 0);
    });

    links.forEach(link => {
      if (!link.source || !link.target) return;
      const sourceId = typeof link.source === 'object' ? link.source.id : link.source;
      const targetId = typeof link.target === 'object' ? link.target.id : link.target;
      if (!adjacency.has(sourceId) || !adjacency.has(targetId)) return;
      adjacency.get(sourceId).add(targetId);
      adjacency.get(targetId).add(sourceId);
      degree.set(sourceId, (degree.get(sourceId) || 0) + 1);
      degree.set(targetId, (degree.get(targetId) || 0) + 1);
    });

    const root = nodes.reduce((best, node) => {
      const d = degree.get(node.id) || 0;
      return !best || d > degree.get(best.id) ? node : best;
    }, null);
    if (!root) return;

    const distances = new Map([[root.id, 0]]);
    const queue = [root.id];
    while (queue.length) {
      const id = queue.shift();
      const dist = distances.get(id);
      adjacency.get(id)?.forEach(neighbor => {
        if (!distances.has(neighbor)) {
          distances.set(neighbor, dist + 1);
          queue.push(neighbor);
        }
      });
    }

    const levels = new Map();
    nodes.forEach(node => {
      const dist = distances.get(node.id);
      const level = typeof dist === 'number' ? dist : Math.max(...distances.values()) + 1;
      if (!levels.has(level)) levels.set(level, []);
      levels.get(level).push(node);
    });

    levels.forEach((group, level) => {
      if (level === 0) {
        group.forEach(node => {
          node.x = centerX;
          node.y = centerY;
        });
        return;
      }
      const radius = ROOM_SIZE * 4 * level;
      const angleStep = (Math.PI * 2) / group.length;
      group.forEach((node, idx) => {
        const angle = idx * angleStep;
        node.x = centerX + Math.cos(angle) * radius;
        node.y = centerY + Math.sin(angle) * radius;
      });
    });
  };

  const capitalizeWords = text =>
    text.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());

  const alertMap = useMemo(() => {
    const map = new Map();
    (insights?.alerts ?? []).forEach(alert => {
      if (!map.has(alert.zone_id)) {
        map.set(alert.zone_id, alert);
      }
    });
    return map;
  }, [insights]);

  const [dimensions, setDimensions] = useState({
    width: window.innerWidth,
    height: window.innerHeight
  });

  const ROOM_SIZE = 60;

  // Transform data
  const graphData = useMemo(() => {
    if (!rawData) return { nodes: [], links: [] };

    const createNode = zoneId => {
      const alert = alertMap.get(zoneId);
      const severity = alert?.severity;
      return {
        id: zoneId,
        name: zoneId.toUpperCase(),
        alertType: alert?.insight_type,
        alertSeverity: severity,
        alertSeverityColor: severity ? severityColorMap[severity] : undefined,
      };
    };

    if (rawData.nodes && rawData.edges) {
      const nodes = rawData.nodes.map(createNode);
      const links = rawData.edges
        .map(edge => {
          const source = edge.from_zone_id ?? edge.source ?? edge.from;
          const target = edge.to_zone_id ?? edge.target ?? edge.to;
          if (!source || !target) return null;

          const transitionCount = getEdgeMetric(edge, 'transition_count');
          const transitionProbability = getEdgeMetric(edge, 'transition_probability');
          return {
            source,
            target,
            transition_count: transitionCount,
            transition_probability: transitionProbability,
            flowScore: transitionCount * Math.max(transitionProbability, 0.1),
            offset: 0,
          };
        })
        .filter(Boolean);

      assignInitialLayout(nodes, links, dimensions);
      return { nodes, links };
    }

    if (!rawData.zones) return { nodes: [], links: [] };

    const timeWindows = rawData.time_windows ?? rawData[' time_windows '] ?? [];
    const latestWindow = timeWindows.reduce((best, window) => {
      const start = getEdgeMetric(window, 'window_start_ms');
      if (!best) return window;
      const bestStart = getEdgeMetric(best, 'window_start_ms');
      return start > bestStart ? window : best;
    }, null);

    const windowZoneMap = new Map();
    const windowGraph = latestWindow ? safeGet(latestWindow, 'window_graph') : null;
    if (windowGraph) {
      (windowGraph.nodes ?? []).forEach(zone => {
        windowZoneMap.set(zone.name, zone);
      });
    }

    const nodes = rawData.zones.map(zone => createNode(zone.name));

    const links = [];
    const edgeSet = new Set();
    rawData.zones.forEach(zone => {
      if (zone.out_edges) {
        zone.out_edges.forEach(edge => {
          edgeSet.add(`${zone.name}||${edge.name}`);
        });
      }
    });

    rawData.zones.forEach(zone => {
      if (zone.out_edges) {
        const windowZone = windowZoneMap.get(zone.name);
        zone.out_edges.forEach(edge => {
          const currentCount = getEdgeMetric(edge, 'transition_count');
          const currentProbability = getEdgeMetric(edge, 'transition_probability');

          let windowCount = 0;
          let windowProbability = 0;
          if (windowZone && windowZone.out_edges) {
            const matching = windowZone.out_edges.find(wEdge => wEdge.name === edge.name);
            if (matching) {
              windowCount = getEdgeMetric(matching, 'transition_count');
              windowProbability = getEdgeMetric(matching, 'transition_probability');
            }
          }

          const transitionCount = windowCount || currentCount;
          const transitionProbability = windowProbability || currentProbability;
          const flowScore = transitionCount * Math.max(transitionProbability, 0.1);
          const reverseKey = `${edge.name}||${zone.name}`;
          const hasReverse = edgeSet.has(reverseKey);
          const offset = hasReverse ? (zone.name.localeCompare(edge.name) < 0 ? 4 : -4) : 0;

          links.push({
            source: zone.name,
            target: edge.name,
            transition_count: transitionCount,
            transition_probability: transitionProbability,
            flowScore,
            offset,
          });
        });
      }
    });

    assignInitialLayout(nodes, links, dimensions);
    return { nodes, links };
  }, [rawData, alertMap, dimensions]);

  useEffect(() => {
    const handleResize = () => setDimensions({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    if (!selectedAlertZone) return;

    setSidebarOpen(true);
    const node = alertRefs.current[selectedAlertZone];
    if (node) {
      node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    const graphNode = graphData.nodes.find(n => n.id === selectedAlertZone);
    if (graphNode && fgRef.current && typeof graphNode.x === 'number' && typeof graphNode.y === 'number') {
      fgRef.current.centerAt(graphNode.x, graphNode.y, 500);
      fgRef.current.zoom(3, 500);
    }

    setHighlightedAlertZone(selectedAlertZone);
    if (highlightTimeoutRef.current) {
      clearTimeout(highlightTimeoutRef.current);
    }
    highlightTimeoutRef.current = setTimeout(() => {
      setHighlightedAlertZone(null);
      setSelectedAlertZone(null);
      highlightTimeoutRef.current = null;
    }, 3000);

    return () => {
      if (highlightTimeoutRef.current) {
        clearTimeout(highlightTimeoutRef.current);
        highlightTimeoutRef.current = null;
      }
    };
  }, [selectedAlertZone, graphData]);

  const alerts = insights?.alerts ?? [];
  const summary = insights?.summary;

  // 2. Tweak the physics engine to make rooms clump together
  useEffect(() => {
    if (fgRef.current && !loading) {
      const COLLIDE_RADIUS = ROOM_SIZE / 2 + 4;

      // Pull nodes tightly together
      fgRef.current.d3Force('link').distance(ROOM_SIZE);

      // Create a collision force sized for the circular node radius
      fgRef.current.d3Force('collide', forceCollide(COLLIDE_RADIUS));

      // Reduce the general repulsion so they form a tighter "building" block
      fgRef.current.d3Force('charge').strength(-50);
    }
  }, [graphData, loading]);

  if (loading) return <div style={{ color: 'white', backgroundColor: '#1a1a1a', height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><h2>Loading Schematic...</h2></div>;


  return (
    <div style={{ position: 'relative', margin: 0, padding: 0, overflow: 'hidden', backgroundColor: '#1e1e1e' }}>
      <div style={{ position: 'absolute', top: 16, right: 16, zIndex: 20 }}>
        <button
          onClick={() => setSidebarOpen(open => !open)}
          style={{
            padding: '10px 16px',
            borderRadius: 24,
            border: 'none',
            cursor: 'pointer',
            backgroundColor: '#2c3e50',
            color: '#ffffff',
            boxShadow: '0 4px 14px rgba(0,0,0,0.3)'
          }}
        >
          {sidebarOpen ? 'Hide insights' : 'Show insights'}
        </button>
      </div>

      <div
        style={{
          position: 'absolute',
          top: 0,
          right: 0,
          height: '100%',
          width: sidebarOpen ? 340 : 0,
          transition: 'width 240ms ease',
          overflow: 'hidden',
          backgroundColor: 'rgba(20, 20, 20, 0.95)',
          color: '#fff',
          zIndex: 15,
          borderLeft: sidebarOpen ? '1px solid rgba(255,255,255,0.08)' : 'none'
        }}
      >
        <div style={{ padding: '20px', minWidth: 340, opacity: sidebarOpen ? 1 : 0, transition: 'opacity 200ms ease' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 18 }}>Insights</h2>
              <p style={{ margin: '6px 0 0', color: '#bdc3c7', fontSize: 13 }}>
                {alerts.length} active notification{alerts.length === 1 ? '' : 's'}
              </p>
            </div>
          </div>

          {summary ? (
            <div style={{ marginBottom: 20, padding: '16px', borderRadius: 14, backgroundColor: 'rgba(255,255,255,0.04)' }}>
              <div style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: 1, color: '#7f8c8d', marginBottom: 8 }}>Summary</div>
              <div style={{ fontSize: 14, lineHeight: 1.5 }}>{summary.message}</div>
            </div>
          ) : null}

          <div style={{ maxHeight: 'calc(100vh - 170px)', overflowY: 'auto', paddingRight: 4 }}>
            {alerts.length === 0 ? (
              <div style={{ color: '#95a5a6', fontSize: 14 }}>No insights available.</div>
            ) : (
              alerts.map(alert => {
                const badgeColor = severityColorMap[alert.severity] || '#95a5a6';
                const isSelected = selectedAlertZone === alert.zone_id;
                const isHighlighted = highlightedAlertZone === alert.zone_id;
                return (
                  <div
                    key={alert.id}
                    ref={el => { if (el) alertRefs.current[alert.zone_id] = el; }}
                    onClick={() => setSelectedAlertZone(alert.zone_id)}
                    style={{
                      cursor: 'pointer',
                      marginBottom: 14,
                      padding: '14px 16px',
                      borderRadius: 14,
                      backgroundColor: isHighlighted ? 'rgba(46, 204, 113, 0.14)' : 'rgba(255,255,255,0.04)',
                      border: isSelected ? '1px solid #2ecc71' : '1px solid transparent',
                      transition: 'background-color 220ms ease, border-color 220ms ease',
                      boxShadow: isHighlighted ? '0 0 0 2px rgba(46, 204, 113, 0.16)' : 'none'
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
                      <span style={{ width: 10, height: 10, borderRadius: '50%', backgroundColor: badgeColor, display: 'inline-block', marginRight: 10 }} />
                      <div>
                        <div style={{ fontSize: 12, color: '#bdc3c7', textTransform: 'uppercase', letterSpacing: 0.8 }}>{alert.severity}</div>
                        <div style={{ fontSize: 16, fontWeight: 600 }}>{capitalizeWords(alert.insight_type)}</div>
                      </div>
                    </div>
                    <div style={{ fontSize: 14, lineHeight: 1.5, color: '#ecf0f1', marginBottom: 10 }}>{alert.message}</div>
                    <div style={{ fontSize: 12, color: '#95a5a6' }}>Zone: {alert.zone_id}</div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      <ForceGraph2D
        ref={fgRef}
        width={dimensions.width}
        height={dimensions.height}
        graphData={graphData}

        linkColor={() => '#95a5a6'}
        linkWidth={2}
        linkCurvature={0}
        linkDirectionalParticles={link => Math.min(8, Math.max(1, Math.round((link.flowScore || 1) / 40)))}
        linkDirectionalParticleWidth={6}
        linkDirectionalParticleColor={link => link.flowScore > 40 ? '#ff4136' : '#2ecc40'}
        linkDirectionalParticleSpeed={link => 0.0008 + Math.min(0.006, (link.transition_probability || 0.1) * 0.0035 + (link.transition_count || 0) / 1600)}
        linkDirectionalParticleOffset={link => link.offset || 0}
        linkDirectionalArrowLength={0}
        nodeLabel={node => node.alertType ? node.alertType : node.name}
        onNodeClick={node => {
          if (node.alertType) {
            setSelectedAlertZone(node.id);
          }
        }}

        // 3. Custom Canvas Rendering
        nodeCanvasObject={(node, ctx, globalScale) => {
          const fontSize = 12 / globalScale;
          const paddingX = 10 / globalScale;
          const paddingY = 6 / globalScale;
          const cornerRadius = 8 / globalScale;
          ctx.font = `${fontSize}px Sans-Serif`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';

          const labelLines = node.name.split(' ');
          const lineWidths = labelLines.map(line => ctx.measureText(line).width);
          const labelWidth = Math.max(...lineWidths);
          const labelHeight = labelLines.length * fontSize * 1.2;
          const rectWidth = labelWidth + paddingX * 2;
          const rectHeight = labelHeight + paddingY * 2;
          const rectX = node.x - rectWidth / 2;
          const rectY = node.y - rectHeight / 2;

          const drawRoundedRect = (x, y, w, h, r) => {
            ctx.beginPath();
            ctx.moveTo(x + r, y);
            ctx.lineTo(x + w - r, y);
            ctx.quadraticCurveTo(x + w, y, x + w, y + r);
            ctx.lineTo(x + w, y + h - r);
            ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
            ctx.lineTo(x + r, y + h);
            ctx.quadraticCurveTo(x, y + h, x, y + h - r);
            ctx.lineTo(x, y + r);
            ctx.quadraticCurveTo(x, y, x + r, y);
            ctx.closePath();
          };

          // B. Draw the "Room" (Node) rectangle
          ctx.save();
          drawRoundedRect(rectX, rectY, rectWidth, rectHeight, cornerRadius);
          ctx.fillStyle = '#2c3e50';
          ctx.fill();
          ctx.strokeStyle = '#ecf0f1';
          ctx.lineWidth = 2 / globalScale;
          ctx.stroke();
          ctx.restore();

          if (node.alertSeverity && node.alertSeverity !== 'resolved') {
            const iconRadius = 6 / globalScale;
            const iconX = node.x;
            const iconY = rectY - iconRadius - 4 / globalScale;
            const badgeColor = node.alertSeverityColor || '#7f8c8d';

            ctx.save();
            ctx.beginPath();
            ctx.fillStyle = badgeColor;
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1.5 / globalScale;
            ctx.arc(iconX, iconY, iconRadius, 0, 2 * Math.PI);
            ctx.fill();
            ctx.stroke();

            ctx.fillStyle = '#ffffff';
            ctx.font = `${9 / globalScale}px Sans-Serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText('!', iconX, iconY + 0.5 / globalScale);
            ctx.restore();
          }

          if (node.id === selectedAlertZone) {
            ctx.save();
            ctx.strokeStyle = 'rgba(46, 204, 113, 0.95)';
            ctx.lineWidth = 4 / globalScale;
            ctx.strokeRect(rectX - 2 / globalScale, rectY - 2 / globalScale, rectWidth + 4 / globalScale, rectHeight + 4 / globalScale);
            ctx.restore();
          }

          // C. Draw the Room Label
          ctx.fillStyle = '#ffffff';
          labelLines.forEach((line, index) => {
            const lineY = node.y - labelHeight / 2 + index * fontSize * 1.2 + fontSize / 2;
            ctx.fillText(line, node.x, lineY);
          });
        }}
      />
    </div>
  );
}