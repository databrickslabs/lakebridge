import React, {type ReactNode} from 'react';
import type FooterType from '@theme/Footer';
import type {WrapperProps} from '@docusaurus/types';

type Props = WrapperProps<typeof FooterType>;

export default function FooterWrapper(props: Props): ReactNode {
  return (
    <footer className="footer">
      <div className="container container-fluid">
        <div className="footer__bottom text--center">
          <div className="footer__copyright">
            © Databricks {(new Date()).getFullYear()}. All rights reserved. Provided by <a href="https://github.com/databrickslabs" target="_blank"
               rel="noopener noreferrer" className="footer__link-item">Databricks Labs
              <svg width="13.5" height="13.5" aria-hidden="true" viewBox="0 0 24 24" className="ml-1">
                <path fill="currentColor" d="M21 13v10h-21v-19h12v2h-10v15h17v-8h2zm3-12h-10.988l4.035 4-6.977 7.07 2.828 2.828 6.977-7.07 4.125 4.172v-11z"></path>
              </svg>
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
