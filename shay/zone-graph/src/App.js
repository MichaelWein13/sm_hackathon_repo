import React, { useState, useEffect, useMemo } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { fetchGraphData, fetchInsights } from './Api'; // Make sure the casing matches your file name

export default function App() {
  // 1. Setup state for our fetched data, loading status, and errors
  const [rawData, setRawData] = useState(null);
  const [insights, setInsights] = useState(null); // Stored for future use!
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // 2. Fetch the data when the component mounts
  useEffect(() => {
    Promise.all([fetchGraphData(), fetchInsights()])
      .then(([graphResponse, insightsResponse]) => {
        setRawData(graphResponse);
        setInsights(insightsResponse);
        setLoading(false);
      })
      .catch(err => {
        console.error("Failed to load data:", err);
        setError(err.message);
        setLoading(false);
      });
  }, []);

  // 3. Transform the fetched JSON into the format required by the graph
  const graphData = useMemo(() => {
    // If data hasn't loaded yet, return empty arrays
    if (!rawData || !rawData.zones) return { nodes: [], links: [] };

    const nodes = rawData.zones.map(zone => ({
      id: zone.name,
      name: zone.name.toUpperCase(),
    }));

    const links = [];
    rawData.zones.forEach(zone => {
      // Safety check in case a zone has no out_edges
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

  // 4. Handle mobile responsiveness by tracking window size
  const [dimensions, setDimensions] = useState({
    width: window.innerWidth,
    height: window.innerHeight
  });

  useEffect(() => {
    const handleResize = () => {
      setDimensions({ width: window.innerWidth, height: window.innerHeight });
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // 5. Render Loading, Error, or the Graph
  if (loading) {
    return (
      <div style={{ color: 'white', backgroundColor: '#1a1a1a', height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <h2>Loading Graph Data...</h2>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ color: '#ff4444', backgroundColor: '#1a1a1a', height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <h2>Error: {error}</h2>
      </div>
    );
  }

  return (
    <div style={{ margin: 0, padding: 0, overflow: 'hidden', backgroundColor: '#1a1a1a' }}>
      <ForceGraph2D
        width={dimensions.width}
        height={dimensions.height}
        graphData={graphData}

        // Node styling
        nodeLabel="name"
        nodeColor={() => '#4facfe'}
        nodeRelSize={8}

        // Link (edge) styling
        linkColor={() => '#ffffff55'}
        linkWidth={2}
        linkDirectionalArrowLength={5}
        linkDirectionalArrowRelPos={1}

        // Render text directly on the canvas below the nodes
        nodeCanvasObject={(node, ctx, globalScale) => {
          const label = node.name;
          const fontSize = 14 / globalScale;
          ctx.font = `${fontSize}px Sans-Serif`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'top';
          ctx.fillStyle = '#ffffff';

          // Draw the node circle
          ctx.beginPath();
          ctx.arc(node.x, node.y, 6, 0, 2 * Math.PI, false);
          ctx.fillStyle = '#4facfe';
          ctx.fill();

          // Draw the text slightly below the node
          ctx.fillStyle = '#ffffff';
          ctx.fillText(label, node.x, node.y + 8);
        }}
      />
    </div>
  );
}