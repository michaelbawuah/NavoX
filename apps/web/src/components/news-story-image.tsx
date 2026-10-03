"use client";

import type { NewsImage } from "@navox/contracts";
import Image from "next/image";
import { useState } from "react";
import { safeNewsImageUrl } from "../lib/news";
import styles from "./news-workspace.module.css";

export function NewsStoryImage({
  image,
  eager = false,
}: {
  image: NewsImage | null | undefined;
  eager?: boolean;
}) {
  if (!image?.alt.trim() || !image.credit.trim()) return null;
  const url = safeNewsImageUrl(image.url);
  return url ? (
    <Photo key={url} image={{ ...image, url }} eager={eager} />
  ) : null;
}

function Photo({ image, eager }: { image: NewsImage; eager: boolean }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <figure className={styles.photo}>
      <Image
        src={image.url}
        alt={image.alt}
        width={1200}
        height={675}
        loading={eager ? "eager" : "lazy"}
        referrerPolicy="no-referrer"
        unoptimized
        onError={() => setFailed(true)}
      />
      <figcaption>{image.credit}</figcaption>
    </figure>
  );
}
