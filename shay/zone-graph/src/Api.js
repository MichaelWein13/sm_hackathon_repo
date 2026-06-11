/**
 * Fetches the zone graph data and time windows.
 * Currently reads from a local JSON file to mock an API response.
 *
 * @returns {Promise<Object>} The parsed JSON data containing zones and time windows.
 */
export async function fetchGraphData() {
  try {
    // Fetch graph data from the API endpoint instead of the local mock graph.json.
    const response = await fetch('/analytics/graph', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({})
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
 * @returns {Promise<Array>} The parsed JSON array containing zone insights.
 */
export async function fetchInsights() {
  try {
    // Fetch insight data from the analytics endpoint.
    // The same endpoint is also used for SSE subscriptions in App.js.
    const response = await fetch('/analytics/insights');

    if (!response.ok) {
      throw new Error(`Failed to fetch insights: HTTP status ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error("Error in fetchInsights:", error);
    throw error;
  }
}