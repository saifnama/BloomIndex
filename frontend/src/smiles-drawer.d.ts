declare module 'smiles-drawer' {
  export interface SvgDrawerOptions {
    width?: number;
    height?: number;
  }

  export class SvgDrawer {
    constructor(options?: SvgDrawerOptions);
    draw(
      data: unknown,
      target: SVGElement | string | null,
      themeName?: string,
    ): SVGElement;
  }

  export function parse(
    smiles: string,
    successCallback: (tree: unknown) => void,
    errorCallback?: (error: unknown) => void,
  ): void;

  const SmilesDrawer: {
    SvgDrawer: typeof SvgDrawer;
    parse: typeof parse;
  };

  export default SmilesDrawer;
}
