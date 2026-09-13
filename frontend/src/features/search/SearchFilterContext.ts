import { createContext } from 'react';

/**
 * Context that signals whether an external filter sidebar is present.
 * When true, SearchBar automatically collapses and hides its internal
 * filter buttons without requiring explicit prop drilling.
 */
export const SearchFilterContext = createContext<boolean>(false);
