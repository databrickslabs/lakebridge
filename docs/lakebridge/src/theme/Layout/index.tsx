import React, {type ReactNode} from 'react';
import {clsx} from "clsx";
import Layout from '@theme-original/Layout';
import type LayoutType from '@theme/Layout';
import type {WrapperProps} from '@docusaurus/types';
import { useLocation } from '@docusaurus/router';

type Props = WrapperProps<typeof LayoutType>;

export default function LayoutWrapper(props: Props): ReactNode {
  const location = useLocation();

  return (
    <div className={clsx("w-full flex flex-col grow", location.pathname === "/lakebridge/" ? "main-page" : "docs-page")}>
      <Layout {...props} />
    </div>
  );
}
