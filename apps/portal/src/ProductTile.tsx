import { href } from "./router";
import { displayName, heroImage, priceText, tileImage, type Tile } from "./shopApi";

/** Every image box is 1:1 with the photo contained, never cropped, on the tile grey (rule 3 of the
 * trial build). With no photo, the box stays plain. The alt text is the full catalog name. */
export function ProductImage({ id, name, photo, size }: { id: string; name: string; photo: boolean; size: "tile" | "hero" }) {
  return (
    <div className="image-box" data-testid="image-box">
      {photo && <img src={size === "hero" ? heroImage(id) : tileImage(id)} alt={name} loading={size === "tile" ? "lazy" : "eager"} />}
    </div>
  );
}

export function ProductTile({ tile }: { tile: Tile }) {
  return (
    <li className="tile" data-product={tile.product_id}>
      <a href={href({ page: "product", id: tile.product_id })}>
        <ProductImage id={tile.product_id} name={tile.name} photo={tile.photo} size="tile" />
        <span className="tile-brand">{tile.brand}</span>
        <span className="tile-name">{displayName(tile.brand, tile.name)}</span>
        <span className="tile-price">{priceText(tile)}</span>
      </a>
    </li>
  );
}

export function ProductGrid({ tiles, label }: { tiles: Tile[]; label: string }) {
  return (
    <ul className="grid" aria-label={label}>
      {tiles.map((tile) => (
        <ProductTile key={tile.product_id} tile={tile} />
      ))}
    </ul>
  );
}
