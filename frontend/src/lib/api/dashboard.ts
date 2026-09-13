import { api } from './client';

export const searchTypesApi = {
  /**
   * Fetches available article classifications for a target search source.
   */
  async getTypes(source: string): Promise<{
    types: { key: string; display_name: string; count: number | null }[];
  }> {
    const response = await api.get('/search/types', { params: { source } });
    return response.data;
  },
};

export const dashboardApi = {
  async getMetrics() {
    const response = await api.get('/api/dashboard/metrics');
    return response.data;
  },
};
