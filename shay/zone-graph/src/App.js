import React, { useState, useEffect, useMemo, useRef } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { forceCollide } from 'd3-force';

import { fetchGraphData, fetchInsights } from './Api'; // Adjust casing to match your file


export default function App() {
  const [rawData, setRawData] = useState(null);
  const [insights, setInsights] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const fgRef = useRef(); // 1. Reference to the graph to tweak its physics

  // Fetch data
  useEffect(() => {
    Promise.all([fetchGraphData(), fetchInsights()])
      .then(([graphResponse, insightsResponse]) => {
        setRawData(graphResponse);
        setInsights(insightsResponse);
        setLoading(false);
      })
      .catch(err => {
        console.error("Failed to load data:", err);
        setLoading(false);
      });
  }, []);

  const severityColorMap = {
    detecting: '#95a5a6',
    warning: '#f1c40f',
    critical: '#e74c3c',
    resolving: '#5dade2'
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

  // Transform data
  const graphData = useMemo(() => {
    if (!rawData || !rawData.zones) return { nodes: [], links: [] };

    const nodes = rawData.zones.map(zone => {
      const alert = alertMap.get(zone.name);
      const severity = alert?.severity;
      return {
        id: zone.name,
        name: zone.name.toUpperCase(),
        alertType: alert?.insight_type,
        alertSeverity: severity,
        alertSeverityColor: severity ? severityColorMap[severity] : undefined,
      };
    });

    const links = [];
    rawData.zones.forEach(zone => {
      if (zone.out_edges) {
        zone.out_edges.forEach(edge => {
          links.push({
            source: zone.name,
            target: edge.name
          });
        });
      }
    });

    return { nodes, links };
  }, [rawData]);

  // Handle mobile responsiveness
  const [dimensions, setDimensions] = useState({
    width: window.innerWidth,
    height: window.innerHeight
  });

  useEffect(() => {
    const handleResize = () => setDimensions({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const ROOM_SIZE = 60;
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
                return (
                  <div key={alert.id} style={{ marginBottom: 14, padding: '14px 16px', borderRadius: 14, backgroundColor: 'rgba(255,255,255,0.04)' }}>
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

        // Hide the default link stroke, but keep links in the render pipeline
        linkWidth={0}
        linkCanvasObjectMode={() => 'before'}
        linkCanvasObject={(link, ctx, globalScale) => {
          const source = typeof link.source === 'object'
            ? link.source
            : graphData.nodes.find(n => n.id === link.source);
          const target = typeof link.target === 'object'
            ? link.target
            : graphData.nodes.find(n => n.id === link.target);

          if (!source || !target || source.x == null || target.x == null) return;

          ctx.save();
          ctx.beginPath();
          ctx.strokeStyle = '#f39c12'; // Door/Corridor color (e.g., orange)
          ctx.lineWidth = 12; // Thick lines to look like hallways
          ctx.moveTo(source.x, source.y);
          ctx.lineTo(target.x, target.y);
          ctx.stroke();
          ctx.restore();
        }}
        nodeLabel={node => node.alertType ? node.alertType : node.name}

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
            const iconRadius = 4 / globalScale;
            const iconX = node.x;
            const iconY = rectY - iconRadius - 4 / globalScale;
            const badgeColor = node.alertSeverityColor || '#7f8c8d';

            ctx.save();
            ctx.beginPath();
            ctx.fillStyle = badgeColor;
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1 / globalScale;
            ctx.arc(iconX, iconY, iconRadius, 0, 2 * Math.PI);
            ctx.fill();
            ctx.stroke();

            ctx.fillStyle = '#ffffff';
            ctx.font = `${8 / globalScale}px Sans-Serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText('!', iconX, iconY + 0.5 / globalScale);
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