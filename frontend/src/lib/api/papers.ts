import { api } from './client';
import type {
  NERResponse,
  PaperData,
  SearchResult,
  SearchFilters,
  Entity,
} from '../../types';

type PaperApiResponse =
  | (PaperData & { entities?: Entity[] })
  | { error: string; sections: unknown[] };

export interface PaperPdfResponse {
  blob: Blob;
  filename: string;
}

export const nerApi = {
  /**
   * Queries literature backends with multi-source filtering and pagination.
   */
  async search(
    query: string,
    filters: SearchFilters,
    page: number = 1,
    source: string = '',
  ): Promise<{
    results: SearchResult[];
    pagination: {
      total: number;
      page: number;
      hasMore: boolean;
      pageSize: number;
    };
    error?: string;
  }> {
    const formData = new FormData();
    formData.append('query', query);
    formData.append('open_access', String(filters.open_access));
    formData.append('has_full_text', String(filters.has_full_text));
    formData.append('article_type', filters.article_type);
    formData.append('sort', filters.sort);
    formData.append('page', String(page));
    if (source) {
      formData.append('source', source);
    }

    const response = await api.post('/search/json', formData);
    return response.data;
  },

  /**
   * Fetches full article content with optional on-demand NER analysis.
   */
  async analysePaper(
    doi: string,
    runNer: boolean = false,
    source: string = '',
  ): Promise<PaperApiResponse> {
    const formData = new FormData();
    formData.append('doi', doi);
    formData.append('run_ner', String(runNer));
    if (source) {
      formData.append('source', source);
    }

    const response = await api.post<PaperApiResponse>('/paper/json', formData, {
      timeout: runNer ? 600000 : 120000,
    });
    return response.data;
  },

  /**
   * Triggers isolated entity extraction pipeline for a given DOI.
   */
  async extractEntities(doi: string): Promise<NERResponse> {
    const response = await api.post('/ner/doi/json', { doi });
    return response.data;
  },

  /**
   * Requests pre-rendered HTML and highlighted content for a paper section.
   */
  async switchSection(
    doi: string,
    sectionIdx: number,
  ): Promise<{ content: string; highlighted: string }> {
    const formData = new FormData();
    formData.append('doi', doi);
    formData.append('section_idx', String(sectionIdx));

    const response = await api.post('/paper/section/json', formData);
    return response.data;
  },
};

const extractFilenameFromDisposition = (
  contentDisposition?: string,
): string => {
  if (!contentDisposition) return 'paper.pdf';
  const match = contentDisposition.match(/filename="?([^";]+)"?/i);
  return match?.[1]?.trim() || 'paper.pdf';
};

export const paperApi = {
  getPdfUrl(identifier: string): string {
    return `/paper/pdf?identifier=${encodeURIComponent(identifier)}`;
  },

  async fetchPdf(identifier: string): Promise<PaperPdfResponse> {
    const response = await api.get('/paper/pdf', {
      params: { identifier },
      responseType: 'blob',
      timeout: 120000,
    });

    return {
      blob: response.data,
      filename: extractFilenameFromDisposition(
        response.headers['content-disposition'],
      ),
    };
  },
};

export interface DbPaperRow {
  title?: string;
  journal?: string;
  year?: number | string;
  doi?: string;
  entity_count?: number;
}

export interface DbPaperList {
  total?: number;
  papers?: DbPaperRow[];
  items?: DbPaperRow[];
}

export const dbApi = {
  async getPapers(
    limit: number = 50,
    offset: number = 0,
    country?: string,
    query?: string,
    year?: number | string,
  ): Promise<DbPaperList | DbPaperRow[]> {
    const response = await api.get('/paper/db/list', {
      params: { limit, offset, country, query, year },
    });
    return response.data;
  },
  async getPaperEntities(doi: string) {
    const response = await api.get(
      `/paper/db/${encodeURIComponent(doi)}/entities`,
    );
    return response.data;
  },
};

export const doiApi = {
  /**
   * Retrieves abstract metadata using multi-source fallbacks
   * (e.g. OpenAlex, Semantic Scholar) when primary repositories miss.
   */
  async getAbstract(doi: string): Promise<{
    doi: string;
    title: string;
    abstract: string;
    authors: string[];
    year: number | null;
    source: string;
    url: string;
  } | null> {
    try {
      console.log('[DOI API] Fetching:', `/doi/abstract?doi=${doi}`);
      const response = await api.get('/doi/abstract', {
        params: { doi },
        timeout: 60000,
      });
      console.log('[DOI API] Response:', response.status, response.data);
      return response.data;
    } catch (e: unknown) {
      const response = (
        e as { response?: { status?: unknown; data?: unknown } } | null
      )?.response;
      console.error(
        '[DOI API] Error:',
        response?.status,
        response?.data ?? (e as { message?: unknown })?.message,
      );
      return null;
    }
  },
};
