import React, {memo, type ReactNode} from 'react';
import {useLocation, useHistory} from 'react-router';
import type {PropSidebarItem} from '@docusaurus/plugin-content-docs';
import {
  DocSidebarItemsExpandedStateProvider,
  useVisibleSidebarItems,
} from '@docusaurus/plugin-content-docs/client';
import DocSidebarItem from '@theme/DocSidebarItem';

import type {Props} from '@theme/DocSidebarItems';

function DocSidebarItems({items, onItemClick, ...props}: Props): ReactNode {
  const location = useLocation();
  const history = useHistory();
  const visibleItems = useVisibleSidebarItems(items, props.activePath);
  
  /**
   * Additional logic for handling custom UI scenarios
   */
  const onClickHandler = (params: PropSidebarItem) => {
    if (onItemClick) {
      onItemClick(params);
    }
    
    // show initial page on menu collapse
    if (params.type === "category") {
      if (location.pathname !== params.href && location.pathname.includes(params.href)) {
        history.push(params.href);
      }
    }
  }
  
  return (
    <DocSidebarItemsExpandedStateProvider>
      {visibleItems.map((item, index) => (
        <DocSidebarItem key={index} item={item} index={index} {...props} onItemClick={onClickHandler} />
      ))}
    </DocSidebarItemsExpandedStateProvider>
  );
}

export default memo(DocSidebarItems);
