import Layout from '@theme/Layout';
import { JSX } from 'react';
import Button from '../components/Button';
import React from 'react';

const Hero = () => {
  return (

    <div className="w-full px-4 md:px-10 flex flex-col justify-center items-center">
      <div className="m-2">
        <img src="img/lakebridge-icon.svg" alt="Lakebridge Logo" className="w-32 md:w-48" />
      </div>

      <h1 className="text-4xl md:text-5xl text-center mb-6">
        Lakebridge - Fast, predictable migrations to Databricks
      </h1>

      <p className="text-lg text-center text-balance">
          Lakebridge is a comprehensive toolkit designed to facilitate seamless migrations to Databricks.
      </p>

      <div className="mt-12 flex flex-wrap justify-center flex-col md:flex-row gap-y-4 md:gap-y-4 md:gap-x-4">
        <Button
          variant="primary"
          link="docs/overview/"
          size="large"
          label="Overview"
          className="bg-[#1b3139] text-white rounded-none border-0 px-6 py-2 font-medium text-base leading-6"
        />
        <Button
          variant="primary"
          link="/docs/installation"
          size="large"
          label="Get Started"
          className="bg-[#eb1600] text-white rounded-none border-0 px-6 py-2 font-medium text-base leading-6"
        />
        <Button
          variant="primary"
          link="docs/transpile/overview"
          size="large"
          label="Learn more"
          className="bg-[#1b3139] text-white rounded-none border-0 px-6 py-2 font-medium text-base leading-6"
        />
      </div>
    </div>
  );
};

export default function Home(): JSX.Element {
  return (
    <Layout>
        <div className='flex grow justify-center mx-auto'>
            <Hero />
        </div>
    </Layout>
  );
}
