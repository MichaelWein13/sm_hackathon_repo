/**
 * Fetches the zone graph data and time windows.
 * Currently reads from a local JSON file to mock an API response.
 *
 * @param {string} source - Data source mode, either 'static' or 'endpoint'.
 * @returns {Promise<Object>} The parsed JSON data containing zones and time windows.
 */
export async function fetchGraphData(source = 'static') {
  try {
    if (source === 'static') {
      const response = await fetch('/final_movement_graph.json');
      if (!response.ok) {
        throw new Error(`Failed to load static graph JSON: HTTP status ${response.status}`);
      }
      return await response.json();
    }

    const response = await fetch('http://localhost:8001/graph', {
      method: 'GET'
    });

    if (!response.ok) {
      throw new Error(`Failed to fetch graph data: HTTP status ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error("Error in fetchGraphData:", error);
    throw error;
  }
}

/**
 * Fetches the generated predictive insights and risk assessments.
 * Currently reads from a local JSON file to mock an API response.
 *
 * @param {string} source - Data source mode, either 'static' or 'endpoint'.
 * @returns {Promise<Array>} The parsed JSON array containing zone insights.
 */
export async function fetchInsights(source = 'static') {
  try {
    if (source === 'static') {
      const response = await fetch('/insights1.json');
      if (!response.ok) {
        throw new Error(`Failed to load static insights JSON: HTTP status ${response.status}`);
      }
      return await response.json();
    }

    // Fetch insight data from the analytics endpoint.
    // The same endpoint is also used for SSE subscriptions in App.js.
    const response = await fetch('http://localhost:8765/analytics/insights');

    if (!response.ok) {
      throw new Error(`Failed to fetch insights: HTTP status ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error("Error in fetchInsights:", error);
    throw error;
  }
}